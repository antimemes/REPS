"""Surface inspect's silent docker provisioning as `status` events.

Running under ``display="none"`` (which the JSONL event channel requires) makes
inspect pass ``--progress quiet`` to every docker compose command and capture the
output of the long ones, so a first run can sit minutes inside a multi-GB image
pull with nothing on the stream — while the one thing compose still prints is a
harmless "No services to build" warning on stderr. Every such subprocess is,
however, wrapped in inspect's ``trace_action(logger, "Subprocess", cmd)``, which
logs structured records (``action``/``detail``/``event``/``duration`` as record
attributes — no message parsing) at TRACE level. This handler listens on the
``inspect_ai`` package logger (it sets ``propagate=False``, so a root handler
would never see these) and re-emits just the provisioning commands — docker
build/pull, plain or compose — as status events with durations. Per-tool-call
``compose exec`` and ``compose up``/``down`` chatter is deliberately excluded:
they fire per sample and the stream is already narrated at that level.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
from collections.abc import Generator

from reps_events import Status, emit

_VERBS = {"build", "pull"}


def docker_verb(detail: str) -> str | None:
    """`"docker compose pull"` / `"docker build"` for a provisioning command,
    None for anything else (including `compose exec`, the per-tool-call path)."""
    try:
        tokens = shlex.split(detail)
    except ValueError:
        return None
    if not tokens or tokens[0] != "docker":
        return None
    prefix, rest = "docker", tokens[1:]
    if rest and rest[0] == "compose":
        prefix, rest = "docker compose", rest[1:]
        # skip compose global flags; every one inspect uses takes a separate
        # value (-f, -p, --ansi, --progress), so consume flag + value pairs
        while rest and rest[0].startswith("-"):
            rest = rest[1:] if "=" in rest[0] else rest[2:]
    if rest and rest[0] in _VERBS:
        return f"{prefix} {rest[0]}"
    return None


class _ProvisioningHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, "action", None) != "Subprocess":
                return
            verb = docker_verb(getattr(record, "detail", "") or "")
            if verb is None:
                return
            event = getattr(record, "event", "")
            if event == "enter":
                emit(Status(detail=f"sandbox: {verb} running…"))
            else:  # exit / cancel / timeout / error
                duration = getattr(record, "duration", None)
                took = (f" ({duration:.1f}s)"
                        if isinstance(duration, (int, float)) else "")
                emit(
                    Status(
                        detail=f"sandbox: {verb} {('done' if event == 'exit' else event)}{took}"
                    )
                )
        except Exception:  # feedback only — never let it touch the eval
            pass


@contextlib.contextmanager
def sandbox_provisioning_status() -> Generator[None]:
    """Attach for the duration of an eval; harmless for sandbox-less tasks."""
    handler = _ProvisioningHandler(level=1)  # TRACE sits below DEBUG
    logger = logging.getLogger("inspect_ai")
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
