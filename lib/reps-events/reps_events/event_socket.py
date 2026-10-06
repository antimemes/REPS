"""Per-run Unix socket: validate a producer event, record it, acknowledge it."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
import json
from pathlib import Path
import socketserver
import tempfile
import threading

from pydantic import ValidationError

from reps_events import PRODUCER_ADAPTER, ProducerPayload
from reps_events.transport import MAX_EVENT_BYTES, TIMEOUT_S


@contextmanager
def event_socket(record: Callable[[ProducerPayload], None]) -> Generator[str]:
    class Handler(socketserver.StreamRequestHandler):
        timeout = TIMEOUT_S

        def handle(self) -> None:
            try:
                line = self.rfile.readline(MAX_EVENT_BYTES + 1)
                if len(line) > MAX_EVENT_BYTES or not line.endswith(b"\n"):
                    raise ValueError(
                        "expected one newline-terminated event, at most 64 MiB"
                    )
                event = PRODUCER_ADAPTER.validate_json(line, strict=True)
                record(event)
                reply = {"ok": True}
            except (ValidationError, ValueError) as exc:
                reply = {"error": str(exc)[:8000]}
            except Exception as exc:
                # Do not acknowledge storage failures as successful delivery.
                reply = {"error": f"event recording failed: {exc}"[:8000]}
            try:
                self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")
            except OSError:
                # A disconnected client may not know that its event was recorded.
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        # Closing waits for active handlers (bounded by their socket timeout).
        daemon_threads = False
        block_on_close = True
        request_queue_size = 128

    # /tmp keeps the pathname short on both macOS and Linux, even if the run
    # directory or the platform's default temporary directory has a long name.
    with tempfile.TemporaryDirectory(prefix="reps-", dir="/tmp") as directory:
        path = str(Path(directory) / "events.sock")
        with Server(path, Handler) as server:
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}
            )
            thread.start()
            try:
                yield path
            finally:
                server.shutdown()
                thread.join()
