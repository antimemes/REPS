"""Typed JSONL analysis with the vendored data models only."""

import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from reps_events import LLMCall, ModelUsage, parse_event, read_events, validate_event
from reps_events.inspect_chat import ChatMessageTool, ContentReasoning, ContentText, ToolCall, ToolCallError

FIXTURE = Path(__file__).parent / "fixtures/llm-call.json"


def test_chat_and_all_output_choices_survive_jsonl(tmp_path):
    event = parse_event(FIXTURE.read_text())
    assert isinstance(event, LLMCall)
    assert isinstance(event.input[-1], ChatMessageTool)
    assert event.input[-1].error.type == "file_not_found"
    assert isinstance(event.output.choices[0].message.content[0], ContentReasoning)
    assert isinstance(event.output.choices[0].message.content[1], ContentText)
    assert event.output.choices[1].message.text == "Alternative"
    assert event.tools[0].parameters["required"] == ["q"]
    assert event.call.request["temperature"] == 0.2
    assert event.call.request["max_tokens"] == 2048
    assert validate_event(json.loads(event.model_dump_json())) == []
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"v": 0, "ts": "2026-09-14T00:00:00Z", "run": "20260916t120000z-012345abcdef",
                               "experiment": "test", "schema": 0,
                               "seq": 0, "event": json.loads(event.model_dump_json())}) + "\n")
    assert next(read_events(path)).event == event


@pytest.mark.parametrize("message", [
    {"role": "alien", "content": "hi"},
    {"role": "tool", "content": "x", "tool_error": "y"},
    {"role": "user", "content": "hi", "source": "input"},
    {"role": "assistant", "content": "hi", "tool_calls": [
        {"id": "t", "function": "f", "arguments": {}, "view": {"format": "text", "content": "hint"}},
    ]},
    {"role": "assistant", "content": "hi", "tool_calls": [
        {"id": "t", "function": "f", "arguments": {}, "type": None},
    ]},
    {"role": "user", "content": [{"type": "image", "image": 123}]},
    {"role": "assistant", "content": "hi", "tool_calls": [{"id": "t", "function": "f", "arguments": "{}"}]},
])
def test_invalid_nested_messages_are_rejected(message):
    with pytest.raises(ValidationError):
        parse_event(json.dumps({"type": "llm.call", "model": "m", "input": [message], "output": {}}))


@pytest.mark.parametrize("field", [
    "input_tokens", "output_tokens", "total_tokens", "input_tokens_cache_write",
    "input_tokens_cache_read", "reasoning_tokens",
])
def test_token_counts_are_non_negative_at_construction_and_ingestion(field):
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ModelUsage(**{field: -1})
    assert getattr(ModelUsage(**{field: 0}), field) == 0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        parse_event(json.dumps({
            "type": "llm.call", "model": "m", "input": [],
            "output": {"usage": {field: -1}},
        }))


def test_analysis_does_not_import_inspect_or_provider_sdks():
    result = subprocess.run([sys.executable, "-c", """
import sys
from reps_events import parse_event
parse_event(open(sys.argv[1]).read())
assert not any(name.split('.')[0] in {'inspect_ai', 'openai', 'opentelemetry'} for name in sys.modules)
""", str(FIXTURE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_llm_call_has_only_boundary_fields():
    assert {field.alias or name for name, field in LLMCall.model_fields.items()} == {
        "type", "model", "input", "tools", "tool_choice", "output", "call",
        "error", "retries", "completed", "working_time", "metadata", "agent",
    }
    event = parse_event(FIXTURE.read_text())
    assert not hasattr(event, "temperature") and not hasattr(event, "max_tokens")


@pytest.mark.parametrize("field,value", [
    ("role", "grader"), ("cache", "read"),
    ("instance_id", "s"), ("repeat", 1), ("params", {"temperature": 0.2}),
])
def test_removed_harness_fields_are_rejected(field, value):
    payload = json.loads(FIXTURE.read_text())
    payload[field] = value
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_event(json.dumps(payload))


def test_retries_default_to_none_and_round_trip():
    event = parse_event(FIXTURE.read_text())
    assert event.retries is None
    assert json.loads(event.model_dump_json(by_alias=True))["retries"] is None
    assert "retries" not in json.loads(event.model_dump_json(by_alias=True, exclude_none=True))
    for retries in (None, 0, 1, 2):
        event.retries = retries
        assert event.retries_ == retries
        wire = event.model_dump_json(by_alias=True, exclude_none=True)
        assert parse_event(wire) == event
        assert json.loads(wire).get("retries") == retries
        assert "retries_" not in json.loads(wire)


@pytest.mark.parametrize("retries", [-1, 1.5, "2", True])
def test_retries_reject_invalid_counts(retries):
    payload = json.loads(FIXTURE.read_text())
    payload["retries"] = retries
    with pytest.raises(ValidationError):
        parse_event(json.dumps(payload))
    event = parse_event(FIXTURE.read_text())
    with pytest.raises(ValidationError):
        event.retries = retries


@pytest.mark.parametrize("legacy", [3, "3"])
def test_retries_read_legacy_metadata_without_rewriting_it(legacy):
    payload = json.loads(FIXTURE.read_text())
    payload["metadata"] = {"reps_experiment.retries": legacy, "reps_experiment.backend": "openai-chat"}
    event = parse_event(json.dumps(payload))
    assert event.retries == 3
    assert event.retries_ is None
    wire = event.model_dump_json(by_alias=True, exclude_none=True)
    assert "retries" not in json.loads(wire)
    assert json.loads(wire)["metadata"] == payload["metadata"]
    assert parse_event(wire).retries == 3


@pytest.mark.parametrize("metadata,expected", [
    ({"reps_experiment.backend": "mock"}, 0),
    ({"reps_experiment.backend": "openai-chat"}, 0),
    ({}, None),
    (None, None),
])
def test_legacy_backend_marker_distinguishes_zero_retries_from_unknown(metadata, expected):
    payload = json.loads(FIXTURE.read_text())
    payload["metadata"] = metadata
    event = parse_event(json.dumps(payload))
    assert event.retries == expected
    assert event.retries_ is None
    wire = event.model_dump_json(by_alias=True, exclude_none=True)
    assert "retries" not in json.loads(wire)
    assert json.loads(wire).get("metadata") == metadata
    assert parse_event(wire).retries == expected


def test_retries_wire_field_and_setter_take_precedence_over_metadata():
    payload = json.loads(FIXTURE.read_text())
    payload.update(retries=0, metadata={"reps_experiment.retries": 3, "reps_experiment.backend": "openai-chat"})
    event = parse_event(json.dumps(payload))
    assert event.retries == 0
    event.retries = 4
    assert event.retries == event.retries_ == 4
    assert json.loads(event.model_dump_json(by_alias=True))["retries"] == 4
    event.retries = None
    assert event.retries_ is None
    assert event.retries == 3


@pytest.mark.parametrize("error_type", ["error", "timeout", "harness_specific"])
def test_tool_error_types_are_open_but_parse_errors_remain_typed(error_type):
    message = ChatMessageTool(content="failed", error=ToolCallError(type=error_type, message="failed"))
    assert ChatMessageTool.model_validate_json(message.model_dump_json()).error.type == error_type
    call = ToolCall(id="t", function="f", arguments={}, parse_error="invalid JSON")
    assert ToolCall.model_validate_json(call.model_dump_json()).parse_error == "invalid JSON"
    with pytest.raises(ValidationError):
        ToolCallError(type=1, message="failed")
