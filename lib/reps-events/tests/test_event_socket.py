"""Real socket connections, concurrent writers, and failure acknowledgements."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import threading

import pytest

from reps_events import CustomEvent, EventTransportError, Result, emit
from reps_events.transport import send_event
from reps_events.event_socket import event_socket


def submit(path, data):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(path)
        conn.sendall(data)
        conn.shutdown(socket.SHUT_WR)
        with conn.makefile("rb") as stream:
            return json.loads(stream.readline())


def test_concurrent_large_events_are_separate_and_acknowledged(monkeypatch):
    records = []
    with event_socket(records.append) as path:
        monkeypatch.setenv("REPS_EVENT_SOCKET", path)

        # Exercise independent connections with large payloads.
        def send(i):
            send_event(
                CustomEvent(
                    kind="large", data={"id": i, "text": str(i) * 200000}
                ).model_dump_json()
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(send, range(16)))
        assert len(records) == 16  # acknowledgement follows recording
        assert sorted(e.data["id"] for e in records) == list(range(16))
        assert all(e.data["text"] == str(e.data["id"]) * 200000 for e in records)
    assert not Path(path).exists()


@pytest.mark.parametrize(
    "data",
    [
        b'{"type":"result","name":"m","value":{}}\n',
        b'{"type":"unknown"}\n',
        b'{"type":"run.status","state":"completed"}\n',
        b"not json\n",
        b"{}",
    ],
)
def test_invalid_submission_gets_error_and_is_not_recorded(data):
    records = []
    with event_socket(records.append) as path:
        assert "error" in submit(path, data)
        assert records == []
        assert submit(path, b'{"type":"result","name":"m","value":1}\n') == {"ok": True}
    assert len(records) == 1


def test_ack_waits_for_recording(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def record(event):
        started.set()
        assert release.wait(5)

    with event_socket(record) as path:
        monkeypatch.setenv("REPS_EVENT_SOCKET", path)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(
                send_event, Result(name="n", value=1).model_dump_json()
            )
            try:
                assert started.wait(5)
                assert not future.done()
            finally:
                release.set()
            future.result(timeout=5)


def test_storage_failure_is_not_acknowledged_as_success(monkeypatch):
    def fail(event):
        raise OSError("disk full")

    with event_socket(fail) as path:
        monkeypatch.setenv("REPS_EVENT_SOCKET", path)
        with pytest.raises(EventTransportError, match="disk full"):
            emit(Result(name="n", value=1))




def test_incomplete_client_times_out_and_socket_cleans_up(monkeypatch):
    import reps_events.event_socket as module

    monkeypatch.setattr(module, "TIMEOUT_S", 0.1)
    with event_socket(lambda event: None) as path:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(path)
            conn.sendall(b"{")
            assert "error" in json.loads(conn.recv(65536))
    assert not Path(path).exists()


@pytest.mark.parametrize("data", [
    b'\xff\xfe\x00\n',
    b'{"type":"status","detail":"\xff"}\n',
    b'{"type":"custom","kind":"binary","data":{"blob":"\xc3\x28"}}\n',
])
def test_non_utf8_submission_is_rejected_without_poisoning_receiver(data):
    records = []
    with event_socket(records.append) as path:
        reply = submit(path, data)
        assert isinstance(reply.get("error"), str)
        assert records == []
        assert submit(path, b'{"type":"status","detail":"still working"}\n') == {"ok": True}
    assert len(records) == 1
    assert records[0].detail == "still working"


def test_python_non_utf8_bytes_fail_before_delivery(monkeypatch):
    from pydantic_core import PydanticSerializationError

    records = []
    with event_socket(records.append) as path:
        monkeypatch.setenv("REPS_EVENT_SOCKET", path)
        event = CustomEvent.model_construct(kind="test.binary", data={"blob": b'\xff\x00'})
        with pytest.raises(PydanticSerializationError):
            emit(event)
        assert records == []
        emit(Result(name="still_working", value=1))
    assert len(records) == 1
    assert records[0].name == "still_working"
