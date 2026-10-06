"""The producer describes its actual interpreter through the shared wire contract."""

import json
import locale
import platform
import sys

import pytest

from reps_events import ProducerPython, emit_producer, parse_event


@pytest.mark.parametrize("seed,expected", [(None, None), ("random", None), ("0", 0), ("42", 42)])
def test_emit_producer_records_interpreter_and_round_trips(event_capture, monkeypatch, seed, expected):
    if seed is None:
        monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    else:
        monkeypatch.setenv("PYTHONHASHSEED", seed)
    emit_producer()
    [wire] = event_capture.read()
    event = parse_event(json.dumps(wire))
    assert isinstance(event, ProducerPython)
    assert event.implementation == platform.python_implementation()
    assert event.version == platform.python_version()
    assert event.executable == sys.executable
    assert event.platform == platform.platform()
    assert event.libc == (" ".join(platform.libc_ver()).strip() or None)
    assert event.locale == locale.getlocale()[0]
    assert event.hash_seed == expected
    assert event.flags == sorted(event.flags)
    assert all(getattr(sys.flags, name) for name in event.flags)
    assert not {"n_fields", "n_sequence_fields", "n_unnamed_fields"} & set(event.flags)
    assert ProducerPython.model_validate_json(event.model_dump_json(exclude_none=True)) == event
    assert event.model_dump(mode="json") == wire


def test_emit_producer_omits_unknown_executable_and_libc(event_capture, monkeypatch):
    monkeypatch.setattr(sys, "executable", "")
    monkeypatch.setattr(platform, "libc_ver", lambda: ("", ""))
    emit_producer()
    [wire] = event_capture.read()
    event = parse_event(json.dumps(wire))
    assert event.executable is None and event.libc is None
    serialized = event.model_dump(mode="json", exclude_none=True)
    assert "executable" not in serialized and "libc" not in serialized
