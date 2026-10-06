"""Typed payload parsing and saved-record readers that migrate before validation."""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any, overload

from pydantic import TypeAdapter

from .models import EVENT_ADAPTER, Envelope, Payload
from .migrate import migrate_record


def parse_event(payload: str | bytes) -> Payload:
    """Deserialize one payload through the public discriminated union."""
    return EVENT_ADAPTER.validate_json(payload, strict=True)


@overload
def parse_record(source: str | bytes, *, payload: None = None) -> Envelope: ...


@overload
def parse_record[T](source: str | bytes, *, payload: type[T] | TypeAdapter[T]) -> Envelope[T]: ...


@overload
def parse_record(source: str | bytes, *, payload: Any) -> Envelope[Any]: ...


def parse_record(source: str | bytes, *, payload: Any = None) -> Envelope[Any]:
    """Migrate one saved JSON envelope, then validate it at the current version.

    Use parse_event for a bare payload with no envelope/version.
    Pass an experiment's payload union or TypeAdapter for typed custom events.
    """
    written: dict[str, Any] = json.loads(source)
    if not isinstance(written, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("record must be a JSON object")
    migrated = migrate_record(written)
    supplied: TypeAdapter[Any] = payload
    adapter = (EVENT_ADAPTER if payload is None else supplied
               if isinstance(payload, TypeAdapter) else TypeAdapter[Any](payload))
    # Preserve strict JSON semantics for payload fields such as citation tuples.
    migrated["event"] = adapter.validate_json(json.dumps(migrated.get("event")), strict=True)
    model = Envelope if payload is None else Envelope[Any]
    return model.model_validate(migrated, strict=True)


class EventReadError(ValueError):
    """A record couldn't be decoded; the message identifies its file and line."""


@overload
def read_events(source: str | Path, *, payload: None = None) -> Iterator[Envelope]: ...


@overload
def read_events[T](source: str | Path, *, payload: type[T] | TypeAdapter[T]) -> Iterator[Envelope[T]]: ...


@overload
def read_events(source: str | Path, *, payload: Any) -> Iterator[Envelope[Any]]: ...


def read_events(source: str | Path, *, payload: Any = None) -> Iterator[Envelope[Any]]:
    """Read one JSONL file or a run directory, yielding current-vocabulary envelopes.

    A run directory contains exactly events.jsonl. Unknown types, invalid fields, and
    truncated JSON raise EventReadError rather than silently dropping evidence.
    A valid partial run need not contain run.end. Each JSON record is migrated
    before validation; its returned v is current, while source files stay untouched.
    Pass an experiment's payload union or TypeAdapter to decode its custom events.
    An explicitly typed TypeAdapter also preserves its union in static type checking.
    """
    supplied: TypeAdapter[Any] = payload
    adapter = (supplied if isinstance(payload, TypeAdapter) else TypeAdapter[Any](payload)
               if payload is not None else None)
    source = Path(source)
    path = source / "events.jsonl" if source.is_dir() else source
    if path.name != "events.jsonl":
        raise ValueError("run streams must be named events.jsonl")
    paths = [path]
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    yield parse_record(line, payload=adapter)
                except ValueError as exc:
                    raise EventReadError(f"{path}:{number}: {exc}") from exc
