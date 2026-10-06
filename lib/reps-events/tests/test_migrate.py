"""Written JSON migrates before the current models validate it."""

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from reps_events import (
    VOCABULARY_VERSION, Envelope, EventReadError, LLMCall, content_filtered_without_model,
    migrate_record, parse_record, read_events,
)
from reps_events.migrate import MigrationError


FIXTURE = Path(__file__).parent / "fixtures" / "azure-content-filter-v0.json"


def legacy_record():
    return {
        "v": 0, "ts": "2026-09-25T04:28:07Z", "run": "20260925t041948z-7564cf814db9",
        "experiment": "govsim", "schema": 0, "seq": 213,
        "event": json.loads(FIXTURE.read_text()),
    }


def test_real_azure_content_filter_migrates_before_validation_without_rewriting_file(tmp_path):
    original = legacy_record()
    wire = json.dumps(original)
    path = tmp_path / "events.jsonl"
    path.write_text(wire + "\n")
    with pytest.raises(ValidationError):
        Envelope.model_validate(original)
    migrated = migrate_record(deepcopy(original))
    record = Envelope.model_validate(migrated)
    event = record.event
    assert record.v == VOCABULARY_VERSION and record.seq == 213
    assert next(read_events(path)) == parse_record(wire) == parse_record(wire.encode()) == record
    assert event.error is None and event.call.error is False
    assert event.call.response == ast.literal_eval(original["event"]["error"].split(" - ", 1)[1])
    assert event.output.model == "DeepSeek-V4-Flash"
    assert len(event.output.choices) == 1
    assert event.output.choices[0].message.content == ""
    assert event.output.choices[0].stop_reason == "content_filter"
    assert event.output.completion == ""
    assert event.output.usage.input_tokens == 1054
    assert event.output.usage.output_tokens == 51
    before = deepcopy(migrated)
    assert migrate_record(migrated) is migrated
    assert migrated == before
    assert json.dumps(original) == wire
    assert path.read_text() == wire + "\n"


class ObservedCall(LLMCall):
    error: None = None


@pytest.mark.parametrize("payload", [ObservedCall, TypeAdapter(ObservedCall)])
def test_experiment_payload_validates_after_migration(tmp_path, payload):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(legacy_record()) + "\n")
    record = parse_record(path.read_bytes(), payload=payload)
    assert isinstance(record.event, ObservedCall)
    assert record.event.error is None
    assert next(read_events(path, payload=payload)) == record


def test_current_records_are_never_reinterpreted():
    record = {**legacy_record(), "v": VOCABULARY_VERSION}
    before = deepcopy(record)
    assert migrate_record(record) is record
    assert record == before
    assert parse_record(json.dumps(record)) == Envelope.model_validate(record)


@pytest.mark.parametrize("version", [2, 100])
def test_future_vocabulary_is_not_relabelled_as_current(version):
    record = {**legacy_record(), "v": version}
    before = deepcopy(record)
    with pytest.raises(ValueError, match=f"cannot migrate vocabulary v={version}"):
        migrate_record(record)
    assert record == before


def test_sparse_azure_filter_retains_unknown_model_and_usage():
    wire = legacy_record()
    wire["event"] = json.loads(FIXTURE.with_name("azure-content-filter-no-model-v0.json").read_text())
    event = parse_record(json.dumps(wire)).event
    assert event.error is None and event.call.error is False
    assert event.output.model == ""
    assert content_filtered_without_model(event.output)
    assert event.output.usage is None
    assert event.call.response["usage"] == {"prompt_tokens": 1132, "total_tokens": 1132}


@pytest.mark.parametrize("call_state", ["present", "null", "absent"])
def test_real_legacy_empty_response_becomes_failed_observation(call_state):
    # Run 20260917t204313z-30d2f1fcc315, seq 637: one of 12 silent empty calls.
    wire = legacy_record()
    wire["event"] = json.loads(FIXTURE.with_name("azure-empty-choices-v0.json").read_text())
    if call_state == "null":
        wire["event"]["call"] = None
    elif call_state == "absent":
        del wire["event"]["call"]
    migrated = migrate_record(deepcopy(wire))
    assert ("call" in migrated["event"]) == (call_state != "absent")
    assert migrated["event"].get("call") == wire["event"].get("call")
    record = parse_record(json.dumps(wire))
    event = record.event
    assert record.v == VOCABULARY_VERSION
    assert event.error == f"Empty choices for model '{event.model}' (version 0 record, not retried)"
    assert event.output.model_dump(mode="json", exclude_none=True) == wire["event"]["output"]
    if call_state == "present":
        assert event.call.error is wire["event"]["call"].get("error")
        assert event.call.response == wire["event"]["call"]["response"]
        assert event.call.request == wire["event"]["call"]["request"]
    else:
        assert event.call is None
    assert parse_record(record.model_dump_json()) == record


@pytest.mark.parametrize("call_state", ["null", "absent"])
def test_legacy_error_choices_without_call_do_not_invent_request(call_state):
    wire = legacy_record()
    if call_state == "null":
        wire["event"]["call"] = None
    else:
        del wire["event"]["call"]
    migrated = migrate_record(wire)
    assert ("call" in migrated["event"]) == (call_state != "absent")
    event = parse_record(json.dumps(migrated)).event
    assert event.error is None and event.call is None
    assert event.output.choices[0].stop_reason == "content_filter"


@pytest.mark.parametrize("body", [
    {"error": {"message": "rate limit"}},
    {"model": "model", "choices": []},
    "non-object body",
    ["non-object", "body"],
])
def test_legacy_non_observations_keep_error_and_recovered_body(body):
    record = legacy_record()
    record["event"]["error"] = f"Error code: 400 - {body!r}"
    result = parse_record(json.dumps(record))
    assert result.event.error == record["event"]["error"]
    assert result.event.call.error is None
    assert result.event.call.response == (body if isinstance(body, dict) else {"body": body})
    if isinstance(body, dict) and body.get("choices") == []:
        assert result.event.output.model == body["model"]


@pytest.mark.parametrize("body,reason", [
    ("not a literal", "not a Python literal"),
    ("__import__('os').system('false')", "not a Python literal"),
    ("", "not a Python literal"),
    ("{'body': {1, 2}}", "not JSON-compatible"),
    ("{'body': b'bytes'}", "not JSON-compatible"),
    ("{'choices': 'malformed'}", "cannot convert .* choices to output"),
    ("{'model': 'model', 'choices': None}", "cannot convert .* choices to output"),
    ("{'model': 'model', 'choices': [None]}", "cannot convert .* choices to output"),
    ("{'model': 'model', 'choices': [{'message': None}]}", "cannot convert .* choices to output"),
])
def test_applicable_migration_must_succeed_and_reader_names_line(tmp_path, body, reason):
    record = legacy_record()
    record["event"]["error"] = f"Error code: 400 - {body}"
    with pytest.raises(MigrationError, match=reason):
        migrate_record(record)
    assert record["v"] == 0
    path = tmp_path / "events.jsonl"
    text = json.dumps(legacy_record()) + "\n" + json.dumps(record) + "\n"
    path.write_text(text)
    with pytest.raises(MigrationError, match=reason):
        parse_record(json.dumps(record))
    with pytest.raises(EventReadError, match=f"events.jsonl:2: .*{reason}") as caught:
        list(read_events(path))
    assert isinstance(caught.value.__cause__, MigrationError)
    assert path.read_text() == text


@pytest.mark.parametrize("error", ["Connection error.", "Request timed out."])
def test_non_prefixed_legacy_errors_remain_unchanged(error):
    record = legacy_record()
    record["event"]["error"] = error
    before = deepcopy(record["event"])
    assert migrate_record(record)["event"] == before
    assert parse_record(json.dumps(record)).event.error == error
    assert record["v"] == VOCABULARY_VERSION
