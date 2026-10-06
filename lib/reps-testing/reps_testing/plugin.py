"""Explicit socket capture fixture using the producer contract's reference receiver."""

from collections.abc import Generator
import threading
from typing import Any

import pytest

from reps_events import ProducerPayload
from reps_events.event_socket import event_socket


class EventCapture:
    """Acknowledged event payloads; read() drains the accumulated records."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, event: ProducerPayload) -> None:
        with self._lock:
            self._events.append(event.model_dump(mode="json", by_alias=True))

    def read(self) -> list[dict[str, Any]]:
        with self._lock:
            events, self._events = self._events, []
            return events


@pytest.fixture
def event_capture(monkeypatch: pytest.MonkeyPatch) -> Generator[EventCapture]:
    """Start the receiver only for tests that request event_capture."""
    capture = EventCapture()
    with event_socket(capture.record) as path:
        monkeypatch.setenv("REPS_EVENT_SOCKET", path)
        yield capture
