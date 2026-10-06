"""`reps-runner preview`: a read-only loopback view of the data directory."""

import json
import shlex
import sys
import threading
import urllib.error
import urllib.request
from importlib import resources

import pytest

from reps_runner import cli, preview

RUN_A = "20260916t120000z-0123456789ab"   # older, completed
RUN_B = "20260917t080000z-fedcba987654"   # newer, still being written
COND_A = "a" * 40 + "-fixture"
COND_B = "b" * 40 + "-other-exp"


def _card(run, experiment, started, state, **extra):
    return {"identity": {"run": run, "experiment": experiment, "schema": 0, "condition": "c" * 40},
            "lifecycle": {"state": state, "started_at": started}, "derived": {"last_seq": 1}, **extra}


def _line(run, seq, event):
    return json.dumps({"v": 1, "ts": "2026-09-16T12:00:00Z", "run": run, "experiment": "fixture",
                       "schema": 0, "seq": seq, "event": event}) + "\n"


@pytest.fixture
def home(tmp_path):
    """Two runs, one mid-write; a damaged card; a run directory without a card."""
    a = tmp_path / "runs" / COND_A / RUN_A
    b = tmp_path / "runs" / COND_B / RUN_B
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "events.jsonl").write_text(_line(RUN_A, 0, {"type": "status", "detail": "working"})
                                    + _line(RUN_A, 1, {"type": "run.end", "state": "completed",
                                                       "duration_s": 1.0, "exit_code": 0}))
    (a / "run.json").write_text(json.dumps(_card(RUN_A, "fixture", "2026-09-16T12:00:00Z", "completed",
                                                 note="card-only field")))
    (b / "events.jsonl").write_text(_line(RUN_B, 0, {"type": "status", "detail": "live"})
                                    + '{"v":1,"ts":"2026-09-17T08:00:02Z","seq":1,"ev')  # still being written
    (b / "run.json").write_text(json.dumps(_card(RUN_B, "other-exp", "2026-09-17T08:00:00Z", "running")))
    bad = tmp_path / "runs" / COND_A / "20260919t000000z-aaaaaaaaaaaa"
    bad.mkdir()
    (bad / "run.json").write_text("{not json")
    (tmp_path / "runs" / COND_A / "20260918t000000z-000000000000" / "workspace").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def server(home):
    srv = preview.PreviewServer(home, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def get(server, path):
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + path, timeout=5) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def test_run_list_is_newest_first_and_card_only(server):
    status, headers, body = get(server, "/api/runs")
    assert status == 200 and headers["Content-Type"] == "application/json"
    cards = json.loads(body)
    assert [(c["run_id"], c["condition_dir"]) for c in cards] == [(RUN_B, COND_B), (RUN_A, COND_A)]
    older = cards[1]
    assert older["note"] == "card-only field"             # the card as written…
    assert older["derived"] == {"last_seq": 1}             # …not recomputed from the stream
    assert set(older) - set(_card("", "", "", "", note=1)) == {"run_id", "condition_dir"}


def test_run_list_survives_a_card_with_a_malformed_lifecycle(server):
    odd = server.home / "runs" / COND_A / "20260920t000000z-bbbbbbbbbbbb"
    odd.mkdir()
    (odd / "run.json").write_text(json.dumps({"lifecycle": "garbage"}))
    status, _, body = get(server, "/api/runs")
    assert status == 200
    assert [c["run_id"] for c in json.loads(body)] == [RUN_B, RUN_A, "20260920t000000z-bbbbbbbbbbbb"]


def test_run_files_are_served_as_written(server):
    status, headers, body = get(server, f"/data/runs/{COND_A}/{RUN_A}/run.json")
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert body == (server.home / "runs" / COND_A / RUN_A / "run.json").read_bytes()
    status, headers, body = get(server, f"/data/runs/{COND_B}/{RUN_B}/events.jsonl")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert body == (server.home / "runs" / COND_B / RUN_B / "events.jsonl").read_bytes()
    assert not body.endswith(b"\n")  # the partial line is the shell's to drop


@pytest.mark.parametrize("path, media", [
    ("/", "text/html"),
    ("/static/index.html", "text/html"),
    ("/static/reps-events.js", "text/javascript"),
    ("/static/preview.css", "text/css"),
])
def test_static_files(server, path, media):
    status, headers, body = get(server, path)
    assert status == 200 and headers["Content-Type"] == media and headers["Cache-Control"] == "no-store"
    assert body == (server.static / (path.rsplit("/", 1)[1] or "index.html")).read_bytes()


@pytest.mark.parametrize("path", [
    "/runs/" + RUN_A,
    "/static/../pyproject.toml",
    "/static/__init__.py",
    "/static/%2e%2e/__init__.py",
    "/data/../etc/passwd",
    "/data/../../../../etc/passwd",
    "/data/runs/",
    "/data/runs",
    f"/data/runs/{COND_A}/{RUN_A}",
    "/api/nope",
    "/etc/passwd",
    "/index.html",
])
def test_nothing_outside_the_two_roots_is_served(server, path):
    status, _, body = get(server, path)   # urllib follows a directory redirect to its 404
    assert status == 404
    assert b"root:" not in body and b"[project]" not in body and b"preview_cli" not in body


def test_server_binds_loopback_only(server):
    host, port = server.server_address[:2]
    assert host == "127.0.0.1" and port > 0
    assert server.url == f"http://127.0.0.1:{port}/"


def test_cli_opens_the_browser_on_the_printed_url(home, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(preview.webbrowser, "open", lambda url: opened.append(url))
    seen = {}

    def serve_forever(self):
        seen.update(home=self.home, host=self.server_address[0])
        raise KeyboardInterrupt

    monkeypatch.setattr(preview.PreviewServer, "serve_forever", serve_forever)
    monkeypatch.setattr(sys, "argv", ["reps-runner", "preview", "--data-dir", str(home)])
    assert cli.main() == 0
    assert seen == {"home": home, "host": "127.0.0.1"}
    out = capsys.readouterr().out
    assert "serving on http://127.0.0.1:" in out
    assert opened == [out.split("serving on ")[1].split()[0]]


def test_cli_no_open_and_port(home, monkeypatch, capsys):
    monkeypatch.setattr(preview.webbrowser, "open", lambda url: pytest.fail("browser opened"))
    port = {}

    def serve_forever(self):
        port["bound"] = self.server_address[1]
        raise KeyboardInterrupt

    monkeypatch.setattr(preview.PreviewServer, "serve_forever", serve_forever)
    assert preview.preview_cli(["--no-open", "--data-dir", str(home), "--port", "0"]) == 0
    assert f"serving on http://127.0.0.1:{port['bound']}/" in capsys.readouterr().out


def test_cli_reports_a_port_in_use(home, capsys):
    taken = preview.PreviewServer(home, 0)
    try:
        assert preview.preview_cli(["--no-open", "--data-dir", str(home),
                                    "--port", str(taken.server_address[1])]) == 2
    finally:
        taken.server_close()
    assert "cannot bind 127.0.0.1" in capsys.readouterr().err


def test_shell_references_the_element_and_the_element_defines_its_surface():
    static = resources.files("reps_runner.preview") / "static"
    shell = (static / "index.html").read_text()
    assert "/static/reps-events.js" in shell and "<reps-events" in shell
    element = (static / "reps-events.js").read_text()
    assert 'customElements.define("reps-events"' in element
    for method in ("append(records)", "replace(seq, record)", "clear()"):
        assert method in element
    assert "attachShadow" in element and "streaming" in element and '"expand"' in element
    code = "\n".join(line for line in element.splitlines() if not line.lstrip().startswith(("*", "/*", "//")))
    assert "fetch(" not in code and "location" not in code  # the host is the transport


def test_package_data_ships_the_static_files():
    static = resources.files("reps_runner.preview") / "static"
    assert sorted(p.name for p in static.iterdir() if p.is_file()) == ["index.html", "preview.css", "reps-events.js"]


def test_run_command_prints_the_preview_hint(tmp_path, monkeypatch, capsys):
    from test_protocol import MANIFEST
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(MANIFEST))
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\nexit 0\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.setenv("REPS_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    data_dir = tmp_path / "my data"
    monkeypatch.setattr(sys, "argv", ["reps-runner", "--set", "x=1", "--non-interactive", "--data-dir", str(data_dir)])
    assert cli.main() == 0
    err = capsys.readouterr().err
    assert "▸ store" in err
    assert f"reps-runner preview --data-dir {shlex.quote(str(data_dir))}" in err
