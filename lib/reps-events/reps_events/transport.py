"""One JSON line and one acknowledgement per Unix stream connection."""

import json
import os
import socket
from collections.abc import Mapping

from .models import Json

TIMEOUT_S = 30.0
MAX_EVENT_BYTES = 64 * 1024 * 1024


class EventTransportError(RuntimeError):
    """An event could not be acknowledged by the runner. Do not retry blindly."""


def send_event(payload: str) -> None:
    path = os.environ.get("REPS_EVENT_SOCKET")
    if not path:
        raise EventTransportError(
            "REPS_EVENT_SOCKET is unset; launch the named experiment app"
        )
    data = payload.encode("utf-8") + b"\n"
    if len(data) > MAX_EVENT_BYTES:
        raise EventTransportError("event exceeds the 64 MiB transport limit")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(TIMEOUT_S)
            conn.connect(path)
            conn.sendall(data)
            with conn.makefile("rb") as reader:
                reply = reader.readline(65537)
            if not reply.endswith(b"\n") or len(reply) > 65536:
                raise EventTransportError(
                    "runner did not return a complete acknowledgement"
                )
            result: Json = json.loads(reply)
            if result != {"ok": True}:
                error = (
                    result.get("error", "invalid acknowledgement")
                    if isinstance(result, Mapping)
                    else "invalid acknowledgement"
                )
                raise EventTransportError(f"runner rejected event: {error}")
    except (OSError, ValueError) as exc:
        raise EventTransportError(
            f"event delivery failed (receipt may be uncertain): {exc}"
        ) from exc
