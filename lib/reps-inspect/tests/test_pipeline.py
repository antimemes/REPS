"""End-to-end: a real keyless mockllm eval through eval -> translate -> summary.

Skipped where inspect_ai can't import (e.g. a bare venv missing the C++ runtime the
numpy wheel needs) — the nix build and devshell provide it; these assert the real
Inspect objects still map the way test_translate's fakes assume.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest


def test_main_emits_producer_first(tmp_path, monkeypatch, event_capture):
    from reps_events import Status, emit
    from reps_inspect import main as entry

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"task": "mock-task", "model": "mockllm/model"}))
    monkeypatch.setattr(sys, "argv", ["reps-inspect-eval", str(config)])

    def run(params):
        emit(Status(detail="running"))
        return 0

    monkeypatch.setattr(entry, "run", run)
    assert entry.main() == 0
    assert [e["type"] for e in event_capture.read()] == ["producer.python", "status"]


@pytest.fixture(scope="module")
def inspect_ok():
    try:
        import inspect_ai  # noqa: F401
    except Exception as exc:  # ImportError, or OSError from the numpy wheel
        pytest.skip(f"inspect_ai unavailable: {exc}")


def _hello_task():
    """The same deterministic task experiments/inspect_evals ships as inspect-hello:
    instruction-following samples whose targets are also substrings of mockllm's
    fixed reply ("Default output from mockllm/model"), so the mock scores 1.0
    keyless and offline."""
    from inspect_ai import Task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import includes
    from inspect_ai.solver import generate

    return Task(
        dataset=MemoryDataset(
            [
                Sample(
                    input='Include the word "model" somewhere in your reply.',
                    target="model",
                ),
                Sample(
                    input='Include the word "output" somewhere in your reply.',
                    target="output",
                ),
            ]
        ),
        solver=generate(),
        scorer=includes(),
    )


def _run_hello(tmp_path):
    from inspect_ai import eval as run_eval

    logs = run_eval(
        _hello_task(),
        model="mockllm/model",
        limit=2,
        log_dir=str(tmp_path / "logs"),
        display="none",
        log_format="eval",
        seed=7,
    )
    return logs[0]


def test_resolve_task_pkg_form_imports_the_callable():
    from reps_inspect.main import resolve_task

    import math

    assert resolve_task("pkg:math:floor") is math.floor
    assert resolve_task("inspect_evals/gsm8k") == "inspect_evals/gsm8k"  # passthrough
    with pytest.raises(ValueError):
        resolve_task("pkg:no-attr-given")


def test_mock_eval_summary(inspect_ok, tmp_path, capsys, event_capture):
    from reps_inspect.translate import emit_all

    log = _run_hello(tmp_path)
    assert log.status == "success"
    summary = emit_all(log, "mockllm/model")
    assert "status" not in summary
    assert summary["samples"] == 2 and summary["completed"] == 2
    assert summary["score"] == 1.0
    assert summary["score_name"] == "includes/accuracy"
    assert summary["tokens_output"] > 0


def test_provenance_emitted_from_real_log(inspect_ok, tmp_path, capsys, event_capture):
    from reps_inspect.translate import emit_all

    emit_all(_run_hello(tmp_path), "mockllm/model")
    events = event_capture.read()
    prov = [
        e for e in events if e.get("kind") == "inspect.provenance"
    ]
    assert len(prov) == 1
    # inspect_ai always reports its own version in the log's packages
    assert "inspect_ai" in prov[0]["data"]["packages"]


def test_llm_call_shape(inspect_ok, tmp_path, capsys, event_capture):
    """llm.call carries typed input/output independently of the raw provider call."""
    from reps_inspect.translate import emit_all

    emit_all(_run_hello(tmp_path), "mockllm/model")
    events = event_capture.read()
    calls = [e for e in events if e["type"] == "llm.call"]
    assert calls, "expected at least one llm.call"
    for c in calls:
        assert isinstance(c["input"], list) and c["input"]
        assert all(isinstance(m, dict) and "role" in m and "content" in m for m in c["input"])
        assert "input_refs" not in c
        assert not {"params", "instance_id", "repeat", "role", "cache"} & c.keys()
        assert c["retries"] is None
        assert "choices" in c["output"]


def test_every_fixture_transcript_event_is_retained_raw(inspect_ok, tmp_path, event_capture):
    from inspect_ai.event import ModelEvent
    from reps_inspect.translate import emit_all

    log = _run_hello(tmp_path)
    emit_all(log, "mockllm/model")
    events = event_capture.read()
    native = [event for sample in log.samples for event in sample.events]
    raw = [event["data"] for event in events if event.get("kind") == "inspect.event"]
    assert len({event.event for event in native}) > 1
    assert raw == [event.model_dump(mode="json", exclude_none=True) for event in native]
    assert {event["event"] for event in raw} == {event.event for event in native}
    assert sum(e["type"] == "llm.call" for e in events) == sum(isinstance(e, ModelEvent) for e in native)
    for index, event in enumerate(events):
        if event.get("kind") == "inspect.event" and event["data"]["event"] == "model":
            assert events[index + 1]["type"] == "llm.call"
            # Inspect's in-memory/log reader path expands the 0.3.200 pool form.
            assert event["data"]["input"] and isinstance(event["data"]["input"][0], dict)
            assert not event["data"].get("input_refs")


def test_print_stream_buffers_lines(capsys, event_capture):
    """PrintStream re-emits complete lines as stdout events (partial writes buffer)."""
    from reps_inspect.main import PrintStream

    ps = PrintStream()
    ps.write("a\nb")
    ps.write("c\n")
    events = event_capture.read()
    assert [(e["type"], e["line"]) for e in events] == [
        ("stdout", "a"),
        ("stdout", "bc"),
    ]


def test_print_capture_tags_the_printing_sample(
    inspect_ok, tmp_path, capsys, event_capture
):
    """A print() from inside a running sample becomes a stdout event tagged with
    that sample — inspect's active-sample contextvar, read at write time."""
    import contextlib
    import sys

    from reps_inspect.main import PrintStream
    from inspect_ai import Task
    from inspect_ai import eval as run_eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import includes
    from inspect_ai.solver import solver

    @solver
    def printer():
        async def solve(state, generate):
            print(f"solving {state.sample_id}")
            return await generate(state)

        return solve

    task = Task(
        dataset=MemoryDataset([Sample(input="say model", target="model", id="p1")]),
        solver=printer(),
        scorer=includes(),
    )
    with contextlib.redirect_stdout(PrintStream()):
        run_eval(
            task,
            model="mockllm/model",
            log_dir=str(tmp_path / "logs"),
            display="none",
            log_format="eval",
            seed=7,
        )
    events = event_capture.read()
    tagged = [
        e for e in events if e.get("type") == "stdout" and e.get("line") == "solving p1"
    ]
    assert tagged, f"no tagged stdout event in {events!r}"
    assert tagged[0]["meta"] == {"instance_id": "p1", "repeat": 1}


def test_score_reproducible(inspect_ok, tmp_path, event_capture):
    from reps_inspect.translate import emit_all

    a = emit_all(_run_hello(tmp_path / "a"), "m")
    b = emit_all(_run_hello(tmp_path / "b"), "m")
    # latency varies run to run; the graded outcome must not
    for k in ("samples", "completed", "score", "score_name"):
        assert a[k] == b[k]


def test_events_conform_to_schema(inspect_ok, tmp_path, capsys, event_capture):
    """Every emitted event validates against `reps-emit schema` (skip if reps-emit
    isn't on PATH — it ships with reps-events, also installed in this environment)."""
    if not shutil.which("reps-emit"):
        pytest.skip("reps-emit not on PATH")
    import jsonschema  # inspect_ai pulls this in

    schema = json.loads(subprocess.check_output(["reps-emit", "schema"], text=True))
    from reps_inspect.translate import emit_all

    emit_all(_run_hello(tmp_path), "mockllm/model")
    events = event_capture.read()
    assert events
    for ev in events:
        jsonschema.validate(ev, schema[ev["type"]])


@pytest.mark.parametrize(
    "eval_status,exit_code", [("success", 0), ("error", 1), ("cancelled", 1)]
)
def test_eval_exit_preserves_partial_summary(
    inspect_ok, tmp_path, monkeypatch, capsys, eval_status, exit_code, event_capture
):
    import importlib
    from types import SimpleNamespace
    import inspect_ai
    from reps_inspect.models import Params

    main_module = importlib.import_module("reps_inspect.main")
    record = SimpleNamespace(status=eval_status, samples=[], error=None)
    monkeypatch.setattr(inspect_ai, "eval", lambda *args, **kwargs: [record])
    monkeypatch.setattr(main_module, "emit_provenance", lambda *args: None)
    monkeypatch.setattr(
        main_module,
        "emit_aggregate",
        lambda *args: {"score": 0.5, "completed": 1, "samples": 2},
    )
    deposited = []
    monkeypatch.setattr(
        main_module, "deposit_log", lambda log, path: deposited.append(log)
    )
    monkeypatch.chdir(tmp_path)
    assert main_module.run(Params(task="fake", model="mockllm/model")) == exit_code
    assert deposited == [record]
    assert any("1/2 samples" in e.get("detail", "") for e in event_capture.read())


@pytest.mark.parametrize("empty_logs", [False, True])
def test_eval_startup_failure_exits_nonzero(
    inspect_ok, tmp_path, monkeypatch, capsys, empty_logs, event_capture
):
    import importlib
    import sys
    import inspect_ai

    main_module = importlib.import_module("reps_inspect.main")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"task": "fake", "model": "mockllm/model"}))
    monkeypatch.setattr(sys, "argv", ["reps-inspect-eval", str(config)])
    monkeypatch.chdir(tmp_path)

    def crash(*args, **kwargs):
        if empty_logs:
            return []
        raise RuntimeError("eval startup crashed")

    monkeypatch.setattr(inspect_ai, "eval", crash)
    assert main_module.main() == 1
    captured = capsys.readouterr()
    assert (
        "inspect eval produced no log" if empty_logs else "eval startup crashed"
    ) in captured.err
    events = event_capture.read()
    assert any(e["type"] == "log" and e["level"] == "error" for e in events)
    assert not any(e["type"] == "result" and e["name"] == "status" for e in events)


def test_live_hook_receives_expanded_model_input(inspect_ok, tmp_path, monkeypatch, event_capture):
    import importlib
    from reps_inspect.models import Params
    from inspect_ai.event import ModelEvent

    class FutureModelEvent(ModelEvent):
        future_live: str = "a future upstream field"

    main_module = importlib.import_module("reps_inspect.main")
    original_emit = main_module.SampleTranscript.emit_ready
    inputs = []
    logs = []

    def observe(self, events, agent, *, final=False):
        copied = list(events)
        if not final:
            for index in range(self.position, len(copied)):
                ev = copied[index]
                if ev.pending:
                    break
                if isinstance(ev, ModelEvent):
                    inputs.append(ev.model_dump(mode="json"))
                    copied[index] = FutureModelEvent.model_validate(ev.model_dump())
        original_emit(self, copied, agent, final=final)

    monkeypatch.setattr(main_module.SampleTranscript, "emit_ready", observe)
    monkeypatch.setattr(main_module, "deposit_log", lambda log, _: logs.append(log))
    monkeypatch.setattr(main_module, "resolve_task", lambda _: _hello_task())
    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    assert main_module.run(Params(task="fixture", model="mockllm/model")) == 0
    assert len(inputs) == 2, "expected both calls to pass through the live hook"
    for payload in inputs:
        assert payload["input_refs"] is None
        assert payload["input"] and all(
            isinstance(message, dict) and "role" in message and "content" in message
            for message in payload["input"]
        ), "live capture must see expanded ChatMessages, not .eval message-pool indices"
    events = event_capture.read()
    assert sum(e["type"] == "llm.call" for e in events) == 2
    assert all(e["agent"] == "mockllm/model" for e in events if e["type"] == "llm.call")
    raw = [e["data"] for e in events if e.get("kind") == "inspect.event"]
    native = [e for sample in logs[0].samples for e in sample.events]
    assert len(raw) == len(native)
    assert {e["event"] for e in raw} == {e.event for e in native}
    for sample in logs[0].samples:
        ids = {e.uuid for e in sample.events}
        observed = [e for e in raw if e["uuid"] in ids]
        assert [{k: v for k, v in e.items() if k != "future_live"} for e in observed] == [
            e.model_dump(mode="json", exclude_none=True) for e in sample.events
        ]
    assert all("future_live" in e for e in raw if e["event"] == "model")
    for index, event in enumerate(events):
        if event.get("kind") == "inspect.event" and event["data"]["event"] == "model":
            assert events[index + 1]["type"] == "llm.call"
    assert not any("emit failed" in e.get("message", "") for e in events)
    [warning] = [e for e in events if "dropped unknown fields" in e.get("message", "")]
    assert warning["level"] == "warn" and "future_live" in warning["message"]
