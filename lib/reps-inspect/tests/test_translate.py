"""Translator tests over REAL inspect_ai objects.

These used to run on duck-typed fakes; they now construct the actual pydantic
models (EvalLog, EvalSample, ModelEvent, …), so they double as contract tests
against the PINNED inspect_ai version — a field the translator reads that
upstream renames or retypes fails here at test time, not mid-eval. The cost is
that construction states every required field; the fixtures below are the
minimal honest instances.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from inspect_ai._util.error import EvalError
from inspect_ai.event import (InfoEvent, ModelEvent, SampleLimitEvent, SandboxEvent,
                              SpanBeginEvent, SpanEndEvent, ToolEvent)
from inspect_ai.log import EvalLog, EvalSample
from inspect_ai.log._log import (EvalConfig, EvalDataset, EvalMetric, EvalResults,
                                 EvalRevision, EvalScore, EvalSpec, EvalStats)
from inspect_ai.model import (ChatMessageAssistant, ChatMessageUser, GenerateConfig,
                              ModelCall, ModelOutput, ModelUsage)
from inspect_ai.model._model_output import ChatCompletionChoice
from inspect_ai.scorer import Score

from reps_inspect import translate


def _output(text: str, *, mid: str, stop: str = "stop",
            usage: ModelUsage | None = None) -> ModelOutput:
    return ModelOutput(
        model="mockllm/model",
        choices=[ChatCompletionChoice(
            message=ChatMessageAssistant(content=text, id=mid), stop_reason=stop)],
        usage=usage)


def _model_event(*, call: ModelCall | None, usage: ModelUsage | None,
                 reply: str, in_id: str, out_id: str,
                 working_time: float | None = 0.05,
                 error: str | None = None) -> ModelEvent:
    return ModelEvent(
        model="mockllm/model",
        input=[ChatMessageUser(content="hi", id=in_id)],
        tools=[], tool_choice="none",
        config=GenerateConfig(max_tokens=1024),
        output=_output(reply, mid=out_id,
                       stop="unknown" if error else "stop", usage=usage),
        call=call, working_time=working_time, error=error)


def _sample(sid: int, *, call: ModelCall | None, score: str,
            reply: str) -> EvalSample:
    ev = _model_event(call=call, usage=ModelUsage(input_tokens=sid, output_tokens=33),
                      reply=reply, in_id=f"mu-{sid}", out_id=f"ma-{sid}")
    return EvalSample(
        id=sid, epoch=1, input="hi", target="default",
        messages=[ChatMessageUser(content="hi", id=f"mu-{sid}"),
                  ChatMessageAssistant(content=reply, id=f"ma-{sid}")],
        output=_output(reply, mid=f"ma-{sid}"),
        events=[ev],
        scores={"includes": Score(value=score, answer="a")})


def _log() -> EvalLog:
    return EvalLog(
        status="success",
        eval=EvalSpec(
            created="2026-01-01T00:00:00+00:00", task="gsm8k", task_version=1,
            task_id="t", run_id="r", model="mockllm/model", config=EvalConfig(),
            dataset=EvalDataset(name="gsm8k", location="hf://openai/gsm8k", samples=2),
            packages={"inspect_ai": "0.3.248", "inspect_evals": "0.15.0"},
            revision=EvalRevision(type="git", origin="", commit="911f0ed"),
            task_registry_name="inspect_evals/gsm8k"),
        results=EvalResults(
            scores=[EvalScore(name="includes", scorer="includes",
                              metrics={"accuracy": EvalMetric(name="accuracy", value=0.5),
                                       "stderr": EvalMetric(name="stderr", value=0.1)})],
            total_samples=2, completed_samples=2),
        stats=EvalStats(started_at="2026-01-01T00:00:00+00:00",
                        completed_at="2026-01-01T00:01:00+00:00",
                        model_usage={"mockllm/model": ModelUsage(input_tokens=3,
                                                                 output_tokens=38)}),
        samples=[
            _sample(1, call=ModelCall.create({"vendor": "wire"}, {"provider": "raw"}),
                    score="C", reply="Default output"),
            _sample(2, call=None, score="I", reply="output"),
        ])


def _capture(event_capture) -> list[dict[str, Any]]:
    return event_capture.read()


def _slim(msg: dict[str, Any]) -> tuple[str, str]:
    """(role, content) — real ChatMessage dumps carry ids and bookkeeping fields;
    the invariants under test are placement and content, not the serializer."""
    return msg["role"], msg["content"]


def test_full_translation_shape(event_capture):
    summary = translate.emit_all(_log(), "mockllm/model")
    events = _capture(event_capture)
    kinds = [e["type"] for e in events]

    assert sum(e.get("kind") == "inspect.event" for e in events) == 2
    assert kinds.count("llm.call") == 2
    ae = [e for e in events if e.get("kind") == "inspect.provenance"]
    assert len([e for e in events if e.get("kind") == "inspect.sample"]) == 2
    assert len([e for e in ae if e["kind"] == "inspect.provenance"]) == 1

    # Native model input/output, raw provider payload under call.
    calls = [e for e in events if e["type"] == "llm.call"]
    assert [_slim(m) for m in calls[0]["input"]] == [("user", "hi")]
    assert "params" not in calls[0]
    assert [e["data"]["config"] for e in events if e.get("kind") == "inspect.event"] == [
        {"max_tokens": 1024}, {"max_tokens": 1024},
    ]
    assert calls[0]["call"]["request"] == {"vendor": "wire"}
    assert _slim(calls[0]["output"]["choices"][0]["message"]) == ("assistant", "Default output")
    assert calls[0]["output"]["choices"][0]["stop_reason"] == "stop"
    assert calls[0]["call"]["response"] == {"provider": "raw"}  # raw call preserved here
    assert calls[1]["call"] is None
    assert calls[0]["output"]["usage"]["input_tokens"] == 1
    assert calls[0]["output"]["usage"]["output_tokens"] == 33

    # per-instance scores ride the instance close-out, NOT result events (a result
    # has no instance scope — N same-named metrics buried the run header)
    inst = [e for e in events if e.get("kind") == "inspect.sample"]
    assert [e["data"]["scores"] for e in inst] == \
        [{"includes": "C"}, {"includes": "I"}]
    assert [e["data"]["repeat"] for e in inst] == [1, 1]
    assert not [e for e in events
                if e["type"] == "result" and e["name"].startswith("score:")]

    # Dynamic scorer metrics remain available without creating undeclared results.
    assert any(e.get("kind") == "inspect.metric"
               and e["data"] == {"name": "includes/accuracy", "value": 0.5} for e in events)

    assert summary == {"samples": 2, "completed": 2,
                       "errors": 0, "score": 0.5, "score_name": "includes/accuracy",
                       "tokens_input": 3, "tokens_output": 38}
    reported = [e for e in events if e["type"] == "result"]
    assert len(reported) == len(summary)
    assert {e["name"]: e["value"] for e in reported} == summary


def test_all_native_events_preserved_in_transcript_order(event_capture):
    sample = _log().samples[0]
    sample.events = [
        SpanBeginEvent(id="outer", name="solver"),
        InfoEvent(data={"native": {"future": [1, None]}}),
        sample.events[0],
        ToolEvent(id="tool-1", function="lookup", arguments={"q": "hi"}, result="found"),
        SandboxEvent(action="exec", cmd="echo hi", result=0, output="hi"),
        SampleLimitEvent(type="token", message="budget", limit=42),
        SpanEndEvent(id="outer"),
    ]
    translate.emit_sample(sample, "caller")
    events = _capture(event_capture)
    raw = [e["data"] for e in events if e.get("kind") == "inspect.event"]
    assert raw == [e.model_dump(mode="json", exclude_none=True) for e in sample.events]
    assert {e["event"] for e in raw} == {e.event for e in sample.events}
    for index, event in enumerate(events):
        if event.get("kind") == "inspect.event" and event["data"]["event"] == "model":
            assert events[index + 1]["type"] == "llm.call"
    assert not any(e["type"].startswith("channel.") for e in events)


def test_llm_calls_tagged_with_sample(event_capture):
    translate.emit_all(_log(), "m")
    calls = [e for e in _capture(event_capture) if e["type"] == "llm.call"]
    assert [c["metadata"]["inspect.sample_id"] for c in calls] == [1, 2]
    assert all(c["metadata"]["inspect.epoch"] == 1 for c in calls)
    assert all(c["agent"] == "m" for c in calls)


@pytest.mark.parametrize("role,expected_agent", [(None, "caller"), ("grader", "grader")])
@pytest.mark.parametrize("cache", [None, "read", "write"])
def test_cached_model_events_and_role_attribution(event_capture, role, expected_agent, cache):
    sample = _log().samples[0]
    event = sample.events[0]
    event.role = role
    event.cache = cache
    event.retries = 2
    translate.emit_sample(sample, "caller")
    raw, call, closeout = _capture(event_capture)
    assert raw["kind"] == "inspect.event" and closeout["kind"] == "inspect.sample"
    assert raw["data"] == event.model_dump(mode="json", exclude_none=True)
    assert call["type"] == "llm.call" and call["agent"] == expected_agent
    assert not {"role", "cache", "params", "config", "instance_id", "repeat"} & call.keys()
    assert call["retries"] is None  # Inspect's runtime count is retained in the raw event.
    assert call["metadata"].get("inspect.cache") == cache
    assert call["metadata"]["inspect.sample_id"] == sample.id
    assert call["metadata"]["inspect.epoch"] == sample.epoch
    assert "max_tokens" not in json.dumps(call)


def test_message_source_and_tool_view_survive_only_in_raw_event(event_capture):
    from inspect_ai.tool._tool_call import ToolCall, ToolCallContent

    sample = _log().samples[0]
    event = sample.events[0]
    event.input[0].source = "operator"
    event.output.choices[0].message.tool_calls = [ToolCall(
        id="t", function="lookup", arguments={}, parse_error="invalid JSON",
        view=ToolCallContent(format="markdown", content="viewer-only hint"),
    )]
    translate.emit_sample(sample, "caller")
    raw, call, _ = _capture(event_capture)
    assert raw["data"]["input"][0]["source"] == "operator"
    assert raw["data"]["output"]["choices"][0]["message"]["tool_calls"][0]["view"]["content"] == "viewer-only hint"
    assert "source" not in call["input"][0]
    tool = call["output"]["choices"][0]["message"]["tool_calls"][0]
    assert "view" not in tool and tool["parse_error"] == "invalid JSON"


# --- error paths: only ever executed on real provider failure, so the tests are the
# only coverage these branches get before live-fire ---------------------------------

def test_errored_model_event_preserves_error(event_capture):
    """The live-fire regression: a failed call's ModelEvent carries `error` as a
    plain string, a placeholder empty output message, no usage, no working_time,
    and the provider's error body under call.response (inspect_ai's error branch).
    The error stays a string, exactly as Inspect records it."""
    ev = _model_event(
        call=ModelCall.create({"vendor": "wire"}, {"error": "model call failed"}),
        usage=None, reply="", in_id="m1", out_id="m2",
        working_time=None, error="model call failed")
    translate.emit_model_event(ev, "agent", "s1", 1)
    [call] = _capture(event_capture)
    assert call["type"] == "llm.call"
    assert call["error"] == "model call failed"
    assert call["call"]["response"] == {"error": "model call failed"}
    assert call["output"]["usage"] is None
    assert call["working_time"] is None


def test_model_call_drops_inspect_file_pool_fields(event_capture):
    raw = ModelCall.create({"messages": [{"role": "user", "content": "hi"}]}, {"reply": "yo"})
    raw.call_refs = [(0, 1)]
    raw.call_key = "messages"
    ev = _model_event(call=raw, usage=None, reply="yo", in_id="m1", out_id="m2")
    translate.emit_model_event(ev, "agent", "s1", 1)
    [call] = _capture(event_capture)
    assert "call_refs" not in call["call"] and "call_key" not in call["call"]
    assert call["call"]["request"] == raw.request
    assert call["call"]["response"] == raw.response


def test_future_model_event_fields_are_filtered_once_per_run(event_capture, monkeypatch):
    ev = _model_event(call=ModelCall.create({"future_request": {"x": None}}, {}),
                      usage=ModelUsage(), reply="yo", in_id="m1", out_id="m2")
    original_dump = ModelEvent.model_dump
    dumped = []

    def future_dump(self, **kwargs):
        payload = original_dump(self, **kwargs)
        payload["invented"] = "future"
        payload[f"future_call_{len(dumped)}"] = True
        payload["input"][0]["future_message"] = True
        payload["input"][0]["content"] = [
            {"type": "text", "text": "hi", "future_content": True},
        ]
        payload["tools"] = [{
            "name": "tool", "description": "test", "parameters": {
                "type": "object", "future_schema_keyword": {"x": None},
            }, "future_tool": True,
        }]
        payload["output"]["future_output"] = True
        payload["output"]["usage"]["future_usage"] = 9
        choice = payload["output"]["choices"][0]
        choice["stop_reason"] = "future_stop"
        choice["future_choice"] = True
        choice["message"]["tool_calls"] = [{
            "id": "t", "function": "tool", "arguments": {"future_argument": None},
            "future_tool_call": True,
        }]
        payload["call"]["future_call"] = True
        payload["metadata"] = {"keep": "evidence"}
        dumped.append(payload)
        return payload

    monkeypatch.setattr(ModelEvent, "model_dump", future_dump)
    with translate.drift_warnings():
        translate.emit_model_event(ev, "agent", "s1", 1)
        translate.emit_model_event(ev, "agent", "s2", 1)
        calls = _capture(event_capture)
        assert len(calls) == 2 and all(c["type"] == "llm.call" for c in calls)
    [warning] = _capture(event_capture)
    assert warning["type"] == "log" and warning["level"] == "warn"
    for path in (
        "invented", "future_call_0", "future_call_1",
        "input[].future_message", "input[].content[].future_content",
        "tools[].future_tool", "output.future_output", "output.usage.future_usage",
        "output.choices[].future_choice", "output.choices[].message.tool_calls[].future_tool_call",
        "call.future_call",
    ):
        assert path in warning["message"]
    assert "future_request" not in warning["message"]
    for call in calls:
        assert "invented" not in call
        assert call["output"]["choices"][0]["stop_reason"] == "unknown"
        assert call["metadata"]["inspect.metadata"] == {"keep": "evidence"}
        assert call["metadata"]["inspect.original_stop_reason"] == "future_stop"
        assert call["call"]["request"] == {"future_request": {"x": None}}
        assert call["tools"][0]["parameters"]["future_schema_keyword"] == {"x": None}
        assert call["output"]["choices"][0]["message"]["tool_calls"][0]["arguments"] == {
            "future_argument": None,
        }
    assert all(p["output"]["choices"][0]["stop_reason"] == "future_stop" for p in dumped)
    assert all(p["metadata"] == {"keep": "evidence"} for p in dumped)

    # A second run in this same process must receive its own warning.
    with translate.drift_warnings():
        translate.emit_model_event(ev, "agent", "s3", 1)
    assert [e["type"] for e in _capture(event_capture)] == ["llm.call", "log"]


@pytest.mark.parametrize("message, originals, expected", [
    ({"role": "user", "content": "hi", "source": "future_source"},
     {}, {}),
    ({"role": "tool", "content": "x", "error": {"type": "future_error", "message": "oh"}},
     {}, {"error": {"type": "future_error", "message": "oh"}}),
    ({"role": "assistant", "content": [
        {"type": "tool_use", "tool_type": "future_tool", "id": "t", "name": "tool",
         "arguments": "{}", "result": "evidence"},
    ]}, {"inspect.original_tool_type": "future_tool"}, {"content": []}),
])
def test_unknown_literals_preserve_original_evidence(
    event_capture, monkeypatch, message, originals, expected,
):
    ev = _model_event(call=None, usage=None, reply="yo", in_id="m1", out_id="m2")
    original_dump = ModelEvent.model_dump

    def future_dump(self, **kwargs):
        payload = original_dump(self, **kwargs)
        payload["input"] = [deepcopy(message)]
        return payload

    monkeypatch.setattr(ModelEvent, "model_dump", future_dump)
    translate.emit_model_event(ev, "agent", "s1", 1)
    [call] = _capture(event_capture)
    assert call["metadata"].items() >= originals.items()
    assert "source" not in call["input"][0]
    for key, value in expected.items():
        assert call["input"][0][key] == value
    if "inspect.original_tool_type" in originals:
        assert call["metadata"]["inspect.original_content"] == {"input[0].content[0]": message["content"][0]}


@pytest.mark.parametrize("location", ["input", "output"])
def test_unknown_content_type_preserves_block_without_losing_call(
    event_capture, monkeypatch, location,
):
    ev = _model_event(call=None, usage=None, reply="yo", in_id="m1", out_id="m2")
    original_dump = ModelEvent.model_dump
    unknown = {"type": "future_content", "evidence": {"items": [1, None, {"x": "y"}]}}
    dumped = []

    def future_dump(self, **kwargs):
        payload = original_dump(self, **kwargs)
        message = (payload["input"][0] if location == "input"
                   else payload["output"]["choices"][0]["message"])
        message["content"] = [
            {"type": "text", "text": "before"}, deepcopy(unknown),
            {"type": "text", "text": "after"},
        ]
        dumped.append(payload)
        return payload

    monkeypatch.setattr(ModelEvent, "model_dump", future_dump)
    translate.emit_model_event(ev, "agent", "s1", 1)
    [call] = _capture(event_capture)
    message = (call["input"][0] if location == "input"
               else call["output"]["choices"][0]["message"])
    path = "input[0]" if location == "input" else "output.choices[0].message"
    assert call["type"] == "llm.call"
    assert [part["text"] for part in message["content"]] == ["before", "after"]
    assert call["metadata"]["inspect.original_content"] == {f"{path}.content[1]": unknown}
    source = (dumped[0]["input"][0] if location == "input"
              else dumped[0]["output"]["choices"][0]["message"])
    assert source["content"][1] == unknown


def test_upstream_metadata_and_drift_notes_remain_separate(event_capture, monkeypatch):
    ev = _model_event(call=None, usage=None, reply="yo", in_id="m1", out_id="m2")
    original_dump = ModelEvent.model_dump

    def future_dump(self, **kwargs):
        payload = original_dump(self, **kwargs)
        choices = payload["output"]["choices"]
        choices.append(deepcopy(choices[0]))
        choices[0]["stop_reason"] = "future_a"
        choices[1]["stop_reason"] = "future_b"
        payload["metadata"] = {"original_stop_reason": "provider metadata"}
        return payload

    monkeypatch.setattr(ModelEvent, "model_dump", future_dump)
    translate.emit_model_event(ev, "agent", "s1", 1)
    [call] = _capture(event_capture)
    assert [choice["stop_reason"] for choice in call["output"]["choices"]] == ["unknown", "unknown"]
    assert call["metadata"]["inspect.metadata"] == {"original_stop_reason": "provider metadata"}
    assert call["metadata"]["inspect.original_stop_reason"] == {
        "output.choices[0].stop_reason": "future_a",
        "output.choices[1].stop_reason": "future_b",
    }


def test_sample_error_lands_on_instance_closeout(event_capture):
    """sample.error is an EvalError object — its `.message` (not its pydantic
    str()) must land on the instance close-out (wire error is `str | None`)."""
    s = _sample(2, call=None, score="I", reply="output")
    s.error = EvalError(message="RuntimeError('boom')",
                        traceback="", traceback_ansi="")
    translate.emit_sample(s, "m")
    inst = [e for e in _capture(event_capture)
            if e.get("kind") == "inspect.sample"]
    assert inst[0]["data"]["error"] == "RuntimeError('boom')"


def test_live_transcript_waits_for_pending_prefix_and_emits_each_event_once(event_capture):
    seen = translate.SampleTranscript("s1", 1)
    ev = _model_event(call=None, usage=ModelUsage(input_tokens=1, output_tokens=2),
                      reply="yo", in_id="m1", out_id="m2")
    pending = ToolEvent(id="tool", function="lookup", arguments={}, pending=True)
    transcript = [pending, ev]
    seen.emit_ready(transcript, "agent")
    assert _capture(event_capture) == []
    pending.pending = None
    seen.emit_ready(transcript, "agent")
    events = _capture(event_capture)
    assert [e["data"]["event"] for e in events if e.get("kind") == "inspect.event"] == ["tool", "model"]
    assert events[-1]["type"] == "llm.call" and events[-1]["agent"] == "agent"
    assert events[-1]["metadata"] == {"inspect.sample_id": "s1", "inspect.epoch": 1}
    seen.emit_ready(transcript, "agent")
    assert _capture(event_capture) == []


def test_emit_sample_skips_what_was_streamed_live(event_capture):
    s = _sample(1, call=None, score="C", reply="Default output")
    transcript = translate.SampleTranscript(s.id, s.epoch)
    transcript.emit_ready(s.events, "m")
    assert [e["type"] for e in _capture(event_capture)] == ["custom", "llm.call"]
    translate.emit_sample(s, "m", transcript=transcript)
    events = _capture(event_capture)
    # only the closing instance remains
    assert not [e for e in events if e["type"] == "llm.call"]
    assert [e["kind"] for e in events] == ["inspect.sample"]


def test_final_transcript_retains_unfinished_and_empty_model_events(event_capture):
    sample = _log().samples[0]
    event = sample.events[0]
    event.pending = True
    event.output = ModelOutput()
    translate.emit_sample(sample, "caller")
    raw, call, _ = _capture(event_capture)
    assert raw["data"]["pending"] is True
    assert call["type"] == "llm.call" and call["output"]["choices"] == []


def test_provenance_records_sliceable_covariates(event_capture):
    translate.emit_all(_log(), "mockllm/model")
    prov = [e for e in _capture(event_capture)
            if e.get("kind") == "inspect.provenance"]
    assert len(prov) == 1
    d = prov[0]["data"]
    # the exact upstream versions advisories will slice on
    assert d["packages"] == {"inspect_ai": "0.3.248", "inspect_evals": "0.15.0"}
    assert d["task"] == "gsm8k" and d["task_version"] == 1
