"""Validate captured test payloads against the public event vocabulary."""

from collections.abc import Iterable, Mapping
from typing import Any

from .models import validate_event


def assert_conformant(events: Iterable[Mapping[str, Any]]) -> int:
    checked = 0
    for event in events:
        type_ = event.get("type")
        errors = validate_event(event)
        if errors:
            raise AssertionError(f"malformed {type_!r} event: {errors}\n{event}")
        checked += 1
    return checked
