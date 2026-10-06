"""Exercise plugin discovery, opt-in activation, and real socket delivery."""


def test_plugin_autoloads_without_conftest_and_restores_environment(pytester, monkeypatch):
    monkeypatch.delenv("REPS_EVENT_SOCKET", raising=False)
    pytester.makepyfile("""
import os
from pathlib import Path
from reps_events import Result, emit

socket_paths = []

def test_without_fixture():
    assert "REPS_EVENT_SOCKET" not in os.environ

def test_with_fixture(event_capture):
    path = os.environ["REPS_EVENT_SOCKET"]
    socket_paths.append(path)
    assert Path(path).exists()
    emit(Result(name="score", value=1))
    assert event_capture.read()[0]["value"] == 1
    assert event_capture.read() == []

def test_after_fixture():
    assert "REPS_EVENT_SOCKET" not in os.environ
    assert socket_paths and not Path(socket_paths[0]).exists()
""")
    pytester.runpytest_subprocess("-q").assert_outcomes(passed=3)
