"""Surface inspect's silent docker provisioning as `status` events.

Running with ``display="none"`` (which the JSONL event channel requires) makes
inspect give ``--progress quiet`` to each docker compose command and capture the
output of the long ones. Thus a first run can sit minutes in a multi-GB
image pull with no events on the stream — while the one thing compose prints is
a harmless "No services to build" warning on stderr. Each such subprocess is,
however, wrapped in inspect's ``trace_action(logger, "Subprocess", cmd)``, which
logs structured records (``action``/``detail``/``event``/``duration`` as record
attributes — no message parsing) at TRACE level. This handler listens on the
``inspect_ai`` package logger (it sets ``propagate=False``, thus a root handler
does not see these) and emits just the provisioning commands — docker build/pull,
with or without compose — as status events with durations. Per-tool-call
``compose exec`` and ``compose up``/``down`` chatter is excluded:
they fire for each sample and the stream is narrated at that level.
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
    None for all other commands (`compose exec`, the per-tool-call path, is
    one)."""
    try:
        tokens = shlex.split(detail)
    except ValueError:
        return None
    if not tokens or tokens[0] != "docker":
        return None
    prefix, rest = "docker", tokens[1:]
    if rest and rest[0] == "compose":
        prefix, rest = "docker compose", rest[1:]
        # skip compose global flags; each one inspect uses takes a separate
        # value (-f, -p, --ansi, --progress), thus consume flag + value pairs
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
        except Exception:  # feedback only — do not let it touch the eval
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
