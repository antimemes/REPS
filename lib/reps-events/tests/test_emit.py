"""Author constructors, lossless metadata, and permissive ingestion contracts."""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from reps_events import (
    CapturedLine,
    CustomEvent,
    RunEnd,
    LLMCall, ModelCall, ModelOutput, ModelUsage, ChatMessageUser, ChatMessageAssistant, ChatCompletionChoice,
    Log,
    Result,
    Status,
    emit,
    json_schemas,
    validate_event,
)


def test_models_emit_wire_shapes(event_capture):
    emit(Status(detail="warming up"))
    emit(Log(message="hello"))
    emit(CustomEvent(kind="govsim.discussion", data={"speaker": "alice", "text": "hi"}))
    emit(CustomEvent(kind="test.artifact", data={"name": "log", "path": "artifacts/log", "bytes": 123}))
    events = event_capture.read()
    assert events[0] == {"type": "status", "detail": "warming up"}
    assert events[1] == {"type": "log", "message": "hello", "level": "info"}
    assert events[2] == {
        "type": "custom",
        "kind": "govsim.discussion",
        "data": {"speaker": "alice", "text": "hi"},
    }
    assert events[3]["data"]["bytes"] == 123
    assert all(validate_event(e) == [] for e in events)


def test_llm_call_wire_omits_optional_nulls_and_round_trips(monkeypatch):
    import importlib

    from reps_events import parse_event
    from reps_events.inspect_chat import ContentText

    event = LLMCall(
        model="mock/model",
        input=[ChatMessageUser(content="hello")],
        output=ModelOutput(choices=[ChatCompletionChoice(
            message=ChatMessageAssistant(content=[ContentText(text="reply")]),
        )]),
    )
    sent = []
    monkeypatch.setattr(importlib.import_module("reps_events.emit"), "send_event", sent.append)
    emit(event)
    [serialized] = sent

    def assert_no_null(value):
        assert value is not None
        if isinstance(value, dict):
            for child in value.values():
                assert_no_null(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_null(child)

    assert_no_null(json.loads(serialized))
    assert parse_event(serialized) == event


def test_nested_provider_metadata_survives(event_capture):
    raw = {"system_fingerprint": "fp_123", "vendor": {"future": [1, None, True]}}
    emit(
        LLMCall(
            agent="alice",
            model="provider/alias",
            input=[ChatMessageUser(content="hi")],
            call=ModelCall(request={"model": "sent-model", "vendor_request": {"custom": 1}}, response=raw),
            output=ModelOutput(model="resolved-model", choices=[ChatCompletionChoice(
                message=ChatMessageAssistant(content="", tool_calls=[]),
            )], usage=ModelUsage(input_tokens=0, output_tokens=2)),
            working_time=0.0,
        )
    )
    event = event_capture.read()[0]
    assert event["call"]["response"] == raw
    assert event["call"]["request"] == {"model": "sent-model", "vendor_request": {"custom": 1}}
    assert event["output"]["model"] == "resolved-model"
    assert event["output"]["usage"]["input_tokens"] == event["working_time"] == 0



@pytest.mark.parametrize(
    "construct",
    [
        lambda: Status(detail=123),
        lambda: Status(detail="ok", detial="typo"),
        lambda: Log(message="x", level="fatal"),
        lambda: Result(name="n", value={"nested": 1}),
        lambda: Result(name="n", value=float("nan")),
        lambda: Result(name="n", value=1, step=0),
        lambda: Result(name="n", value=1, unit="count"),
        lambda: CustomEvent(kind=7, data={}),
        lambda: LLMCall(model="m", input="nope", output=ModelOutput()),
        lambda: LLMCall(model="m", input=[], output=ModelOutput(), prams={}),
        lambda: ChatMessageUser(content=123),
        lambda: ModelUsage(input_tokens=True),
        lambda: LLMCall(model="m", input=[], output=ModelOutput(), error={"kind": "timeout"}),
    ],
)
def test_constructor_rejects_invalid_data(construct):
    with pytest.raises(ValidationError):
        construct()


def test_assignment_and_nested_mutation_are_checked(event_capture):
    event = Result(name="n", value=1)
    with pytest.raises(ValidationError):
        event.value = {}
    event = LLMCall(model="m", input=[], output=ModelOutput())
    event.input.append({"role": "alien", "content": "invalid"})
    with pytest.raises((ValidationError, PydanticSerializationError)):
        emit(event)
    assert event_capture.read() == []


def test_custom_events_use_public_container(event_capture):
    emit(CustomEvent(kind="werewolf.night", data={"victim": "alice", "unknown": None}))
    assert event_capture.read()[0] == {
        "type": "custom",
        "kind": "werewolf.night",
        "data": {"victim": "alice", "unknown": None},
    }


def test_private_models_and_lifecycle_cannot_be_emitted(event_capture):
    class Night(BaseModel):
        type: Literal["werewolf.night"] = "werewolf.night"
        victim: str

    class PrivateStatus(Status):
        pass

    for event in (
        Night(victim="alice"),
        PrivateStatus(detail="x"),
        RunEnd(state="completed", duration_s=0, exit_code=0),
        {"type": "status", "detail": "x"},
    ):
        with pytest.raises(TypeError, match="public producer model"):
            emit(event)
    assert event_capture.read() == []


def test_deferred_models_are_not_exported():
    import reps_events
    from reps_events import models

    for name in ("Metric", "AgentEvent", "Instance", "InstanceData", "Artifact"):
        assert not hasattr(reps_events, name)
        assert not hasattr(models, name)
        assert name not in reps_events.__all__


def test_ingestion_rejects_unknown_fields_without_mutating_evidence():
    event = {
        "type": "llm.call",
        "model": "m",
        "future": True,
        "input": [],
        "output": {"system_fingerprint": "fp"},
    }
    before = json.dumps(event)
    assert validate_event(event)
    assert json.dumps(event) == before
    event["input"] = "bad"
    assert validate_event(event)
    for opaque in ({"type": "custom"}, {"no": "type"}, {"type": ["bad"]}):
        assert validate_event(opaque)


def test_concurrent_emitters_use_socket(event_capture):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: emit(Result(name="n", value=i)), range(100)))
    assert sorted(e["value"] for e in event_capture.read()) == list(range(100))


def test_json_schema_exposes_wire_discriminators_and_constraints():
    schemas = json_schemas()
    assert set(schemas) == {
        "status",
        "log",
        "stdout",
        "stderr",
        "result",
        "llm.call",
        "custom",
        "run.start",
        "run.end",
        "producer.python",
    }
    assert schemas["custom"]["properties"]["type"]["const"] == "custom"
    assert "data" in schemas["custom"]["required"]
    assert schemas["custom"]["additionalProperties"] is False
    assert schemas["llm.call"]["$defs"]["ModelUsage"]["properties"]["input_tokens"]["type"] == "integer"
    assert schemas["llm.call"]["properties"]["input"]["items"]["discriminator"]["propertyName"] == "role"



def test_emission_validation_cannot_rewrite_payload(event_capture, monkeypatch):
    event = CustomEvent(kind="observation", data={"value": None, "number": 1.0})
    original = event.model_dump_json(by_alias=True)
    from reps_events.models import PRODUCER_ADAPTER

    validate = PRODUCER_ADAPTER.validate_json

    def normalize(payload, **kwargs):
        parsed = validate(payload, **kwargs)
        parsed.data["number"] = 999
        return parsed

    import importlib
    from types import SimpleNamespace

    module = importlib.import_module("reps_events.emit")
    monkeypatch.setattr(
        module, "PRODUCER_ADAPTER", SimpleNamespace(validate_json=normalize)
    )
    emit(event)
    assert event_capture.read() == [json.loads(original)]
    assert event.model_dump_json(by_alias=True) == original


def test_union_catches_constraints_that_serialization_accepts(event_capture):
    # A negative count serializes as an integer but violates the usage constraint.
    event = LLMCall.model_construct(
        model="m", input=[], output=ModelOutput.model_construct(
            usage=ModelUsage.model_construct(input_tokens=-1),
        ),
    )
    with pytest.raises(ValidationError):
        emit(event)
    assert event_capture.read() == []


def test_custom_data_requires_json_values():
    with pytest.raises(ValidationError):
        CustomEvent(kind="bad", data={"value": object()})
