"""One model-based emission boundary, with socket transport."""

from __future__ import annotations

import locale
import os
import platform
import sys
from typing import Any

from .transport import send_event
from .models import CustomEvent, PRODUCER_ADAPTER, PRODUCER_MODELS, ProducerPayload, ProducerPython


def emit_producer() -> None:
    """Record this interpreter. Call once per run, before other producer events."""
    try:
        hash_seed = int(os.environ["PYTHONHASHSEED"])
    except (KeyError, ValueError):
        hash_seed = None
    emit(ProducerPython(
        implementation=platform.python_implementation(),
        version=platform.python_version(),
        executable=sys.executable or None,
        platform=platform.platform(),
        libc=" ".join(platform.libc_ver()).strip() or None,
        locale=locale.getlocale()[0],
        hash_seed=hash_seed,
        flags=sorted(name for name in dir(sys.flags)
                     if not name.startswith(("_", "n_"))
                     and isinstance(getattr(sys.flags, name), int) and getattr(sys.flags, name)),
    ))


def emit(event: ProducerPayload | CustomEvent[Any]) -> None:
    """Validate and serialize an event, preserving open metadata verbatim.

    Serialize once, validate the exact wire representation, then write those
    same bytes. Validation may raise but its normalized result is discarded.
    Public producers and typed CustomEvent subclasses are accepted. Custom events
    validate against their own model and the shared wire container. The runner
    alone produces lifecycle events.
    """
    if type(event) not in PRODUCER_MODELS.values() and not isinstance(event, CustomEvent):
        raise TypeError(
            "emit expects a public producer model; use CustomEvent for experiment-specific data"
        )
    payload = event.model_dump_json(by_alias=True, exclude_none=True, warnings="error")
    if isinstance(event, CustomEvent):
        type(event).model_validate_json(payload, strict=True)
    PRODUCER_ADAPTER.validate_json(payload, strict=True)
    send_event(payload)
