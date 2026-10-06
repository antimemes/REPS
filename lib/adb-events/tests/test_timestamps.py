"""The saved timestamp format is fixed-width UTC, including zero microseconds."""

from datetime import datetime, timedelta, timezone
import json

import duckdb
import pytest
from pydantic import ValidationError

from adb_events import VOCABULARY_VERSION, Envelope, LLMCall, ModelOutput, read_events


def record(value):
    return Envelope(
        v=VOCABULARY_VERSION, ts=value, run="20260916t120000z-012345abcdef", experiment="test", schema=0, seq=0,
        event=LLMCall(model="m", input=[], output=ModelOutput(), completed=value),
    )


@pytest.mark.parametrize("value, expected", [
    (datetime(2026, 9, 14, 12, tzinfo=timezone.utc), "2026-09-14T12:00:00.000000Z"),
    ("2026-09-14T12:00:00.123456Z", "2026-09-14T12:00:00.123456Z"),
    ("2026-09-14T14:00:00.1+02:00", "2026-09-14T12:00:00.100000Z"),
    (datetime(2026, 9, 14, 7, tzinfo=timezone(timedelta(hours=-5))),
     "2026-09-14T12:00:00.000000Z"),
])
def test_timestamps_round_trip_in_shared_fixed_width_format(value, expected):
    envelope = record(value)
    serialized = envelope.model_dump_json(exclude_none=True)
    wire = json.loads(serialized)
    assert wire["ts"] == wire["event"]["completed"] == expected
    assert len(wire["ts"]) == 27
    assert envelope.ts.tzinfo is timezone.utc
    assert envelope.event.completed.tzinfo is timezone.utc
    assert Envelope.model_validate_json(serialized) == envelope
    assert isinstance(envelope.model_dump()["event"]["completed"], datetime)


@pytest.mark.parametrize("value", [datetime(2026, 9, 14), "2026-09-14T12:00:00"])
def test_naive_timestamps_are_rejected(value):
    with pytest.raises(ValidationError, match="timezone"):
        Envelope(v=VOCABULARY_VERSION, ts=value, run="20260916t120000z-012345abcdef", experiment="test", schema=0, seq=0,
                 event=LLMCall(model="m", input=[], output=ModelOutput()))
    with pytest.raises(ValidationError, match="timezone"):
        LLMCall(model="m", input=[], output=ModelOutput(), completed=value)
    if isinstance(value, str):
        with pytest.raises(ValidationError, match="timezone"):
            Envelope.model_validate_json(json.dumps({
                "v": VOCABULARY_VERSION, "ts": value, "run": "20260916t120000z-012345abcdef", "experiment": "test", "schema": 0, "seq": 0,
                "event": {"type": "llm.call", "model": "m", "input": [], "output": {}},
            }))


def test_timestamp_strings_sort_in_time_order():
    values = [
        "2026-09-14T00:00:00.1Z", "2026-09-14T00:00:00Z",
        "2026-09-14T02:00:00.000001+02:00", "2026-09-13T23:59:59.999999Z",
        "2026-09-14T00:00:01Z", "2027-01-01T00:00:00Z",
    ]
    records = [record(value) for value in values]
    assert sorted(r.model_dump(mode="json")["ts"] for r in records) == [
        r.model_dump(mode="json")["ts"] for r in sorted(records, key=lambda r: r.ts)
    ]


def test_written_jsonl_is_readable_and_duckdb_infers_timestamp(tmp_path):
    records = [record("2026-09-14T12:00:00Z"), record("2026-09-14T12:00:00.123456Z")]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(r.model_dump_json(exclude_none=True) + "\n" for r in records))
    assert list(read_events(path)) == records
    with duckdb.connect() as connection:
        table = connection.read_json(str(path))
        assert dict(zip(table.columns, map(str, table.types)))["ts"] == "TIMESTAMP"
        assert table.select("ts").fetchall() == [
            (r.ts.replace(tzinfo=None),) for r in records
        ]
