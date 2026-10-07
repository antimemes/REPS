"""`reps-runner preview`: a local, read-only viewer for the runs in the data directory.

Serves two directories on loopback and opens the browser on the shell in `static/`.
The shell lists runs from `/api/runs` and reads a run's `run.json` and `events.jsonl`
straight from `/data/`; the embeddable `<reps-events>` element renders the stream.
With `--manifests DIR` (the built catalog, `nix-build --no-out-link -A manifests`) the
shell also lists the experiments, shows each one's params and results, and renders a
command line for a condition; the server only hands the manifests over.

    /              static/index.html
    /static/…      this package's static/ directory
    /data/…        the data directory (files only; directories are 404)
    /api/runs      every runs/*/*/run.json card, newest first
    /api/manifests every <name>.json in the manifests directory, by name ([] without one)
    /api/runs/<condition_dir>/<run_id>/events?from=N
                 the complete lines of that run's events.jsonl with seq >= N, as
                 ndjson; 204 when there are none, so a poll of a quiet run costs
                 nothing. A trailing line without its newline is still being
                 written and waits for the next poll; a complete line that is not
                 a JSON object with an integer seq is skipped. Each record is
                 migrated to the current vocabulary before it is sent, as every
                 reader of the stream does (reps_events.read); the file on disk
                 is untouched, and a record that cannot be migrated is sent as
                 written, its `v` showing which version it still speaks.

Path handling is the standard library's: `..` segments never leave the two roots.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from contextlib import ExitStack
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from reps_events.migrate import MigrationError, migrate_record

from ..store import resolve_data_dir

_EVENTS_ROUTE = re.compile(r"^/api/runs/([^/]+)/([^/]+)/events$")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")   # one directory name; never "." or ".."


def list_runs(home: Path) -> list[dict[str, Any]]:
    """Every readable card as written, plus the two directory names, newest first."""
    cards: list[dict[str, Any]] = []
    for path in (home / "runs").glob("*/*/run.json"):
        try:
            card = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # being written, or damaged: skip, not fatal
        if isinstance(card, dict):
            cards.append({**card, "run_id": path.parent.name, "condition_dir": path.parent.parent.name})
    cards.sort(key=_started, reverse=True)
    return cards


def list_manifests(catalog: Path | None) -> list[dict[str, Any]]:
    """Every readable manifest in the catalog directory as written, by name."""
    if catalog is None:
        return []
    manifests: list[dict[str, Any]] = []
    for path in sorted(catalog.glob("*.json")):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict):
            typed: dict[str, Any] = {**manifest}
            if isinstance(typed.get("name"), str):
                manifests.append(typed)
    return manifests


def read_events_from(run_dir: Path, start: int) -> bytes:
    """The stream's complete lines with `seq >= start`, migrated to the current
    vocabulary (a line that cannot be migrated goes out as written)."""
    try:
        data = (run_dir / "events.jsonl").read_bytes()
    except OSError:
        return b""
    lines = data.split(b"\n")
    if not data.endswith(b"\n"):
        lines.pop()  # still being written
    out: list[bytes] = []
    for line in lines:
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        seq = record.get("seq") if isinstance(record, dict) else None
        if isinstance(seq, int) and seq >= start:
            try:
                line = json.dumps(migrate_record(record)).encode()
            except MigrationError:
                pass
            out.append(line + b"\n")
    return b"".join(out)


def _started(card: dict[str, Any]) -> tuple[str, str]:
    lifecycle = card.get("lifecycle")
    started = lifecycle.get("started_at") if isinstance(lifecycle, dict) else None
    return (started if isinstance(started, str) else "", card["run_id"])


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, home: Path, port: int = 0, manifests: Path | None = None):
        self.home = home
        self.manifests = manifests
        self._resources = ExitStack()
        self.static = self._resources.enter_context(as_file(files(__package__) / "static"))
        super().__init__(("127.0.0.1", port), PreviewHandler)

    def server_close(self) -> None:
        super().server_close()
        self._resources.close()

    @property
    def url(self) -> str:
        return f"http://{self.server_address[0]}:{self.server_address[1]}/"


class PreviewHandler(SimpleHTTPRequestHandler):
    server: PreviewServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # the browser polls these files
        super().end_headers()

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path
        if path == "/api/runs":
            self._send(json.dumps(list_runs(self.server.home)).encode(), "application/json")
        elif path == "/api/manifests":
            self._send(json.dumps(list_manifests(self.server.manifests)).encode(), "application/json")
        elif match := _EVENTS_ROUTE.match(path):
            condition_dir, run_id = match.groups()
            if not (_SEGMENT.match(condition_dir) and _SEGMENT.match(run_id)):
                return self.send_error(HTTPStatus.NOT_FOUND)
            run_dir = self.server.home / "runs" / condition_dir / run_id
            if not (run_dir / "events.jsonl").is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            raw = parse_qs(url.query).get("from", ["0"])[-1]
            if not raw.isdigit():
                return self.send_error(HTTPStatus.BAD_REQUEST, "from must be a non-negative integer")
            body = read_events_from(run_dir, int(raw))
            if not body:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            else:
                self._send(body, "application/x-ndjson")
        elif path == "/" or path.startswith(("/static/", "/data/")):
            super().do_GET()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path: str) -> str:
        if urlsplit(path).path == "/":
            path = "/static/index.html"
        root, _, rest = path.lstrip("/").partition("/")
        self.directory = str(self.server.home if root == "data" else self.server.static)
        return super().translate_path("/" + rest)

    def list_directory(self, path: str) -> None:  # type: ignore[override]
        self.send_error(HTTPStatus.NOT_FOUND)
        return None


def preview_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reps-runner preview",
                                     description="view the runs in the local data directory in a browser")
    parser.add_argument("--data-dir", metavar="DIR",
                        help="run data directory (default $REPS_DATA_DIR, then $XDG_DATA_HOME/reps or ~/.local/share/reps)")
    parser.add_argument("--port", type=int, default=0, metavar="N",
                        help="port to bind on 127.0.0.1 (default: any free port)")
    parser.add_argument("--no-open", action="store_true", help="print the URL without opening a browser")
    parser.add_argument("--manifests", metavar="DIR",
                        help="the built manifest catalog (nix-build --no-out-link -A manifests): lists the "
                             "experiments, their params and results, and renders run commands")
    args = parser.parse_args(argv)
    home = resolve_data_dir(args.data_dir)
    manifests = Path(args.manifests) if args.manifests else None
    if manifests is not None and not manifests.is_dir():
        print(f"preview: --manifests {args.manifests}: not a directory", file=sys.stderr)
        return 2
    try:
        server = PreviewServer(home, args.port, manifests)
    except OSError as exc:
        print(f"preview: cannot bind 127.0.0.1:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    with server:
        print(f"preview: {home}" + (f"\npreview: manifests {manifests}" if manifests else "")
              + f"\npreview: serving on {server.url}  (Ctrl-C to stop)", flush=True)
        if not args.no_open:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
