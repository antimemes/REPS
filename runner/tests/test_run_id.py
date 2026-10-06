from datetime import datetime, timezone
import re

import pytest
from pydantic import TypeAdapter, ValidationError

from reps_events.identity import RunId
from reps_runner import run_id


def test_launch_clock_is_utc_and_random_suffix_is_independent(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return datetime(2026, 9, 16, 1, 2, 3, tzinfo=tz)

    monkeypatch.setattr(run_id, "datetime", Clock)
    ids = {run_id.new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(re.fullmatch(r"20260916t010203z-[0-9a-f]{12}", value) for value in ids)
    assert all(TypeAdapter(RunId).validate_python(value) == value for value in ids)


@pytest.mark.parametrize("value", [
    "01M2NHT918HEHKNHE3XTMB0J56", "20260916T010203Z-012345abcdef",
    "20260916t010203z-012345ABCDEf", "20260916t010203z-012345abcde",
    "20260916t010203z-012345abcdef0", "20260916t010203z-012345abcdef\n",
    "x20260916t010203z-012345abcdef", "20260916t010203z-012345abcdef/x",
])
def test_only_exact_run_id_syntax_is_accepted(value):
    with pytest.raises(ValidationError):
        TypeAdapter(RunId).validate_python(value)
