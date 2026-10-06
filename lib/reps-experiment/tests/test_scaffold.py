"""The experiment process must report crashes without losing event evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_crash_exits_nonzero_and_keeps_partial_events_and_fallback(tmp_path, event_capture):
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, REPS_RUN_DIR=str(tmp_path),
               PYTHONPATH=os.pathsep.join([str(root / 'lib' / 'reps-experiment'),
                                          str(root / 'lib' / 'reps-events')]))
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from reps_events import Result, emit
from reps_experiment import experiment_main
class Params:
    @classmethod
    def model_validate(cls, raw):
        return raw

def run(params):
    emit(Result(name="observations", value=params["observations"]))
    raise RuntimeError("simulation crashed after observation")

raise SystemExit(experiment_main(Params, run, prog="crashing-experiment",
                                fallback_summary={"errors": 1}))
""",
        ],
        input='{"observations": 3}',
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "RuntimeError: simulation crashed after observation" in proc.stderr
    events = event_capture.read()
    assert events[0]["type"] == "producer.python"
    assert [(e['name'], e['value']) for e in events[1:]] == [('observations', 3), ('errors', 1)]


def test_experiment_main_emits_producer_first(tmp_path, event_capture):
    from reps_events import Status, emit
    from reps_experiment import experiment_main
    from pydantic import BaseModel

    class Params(BaseModel):
        n: int

    config = tmp_path / "config.json"
    config.write_text('{"n": 1}')
    assert experiment_main(Params, lambda _: emit(Status(detail="running")),
                           prog="mock", argv=[str(config)]) == 0
    assert [e["type"] for e in event_capture.read()] == ["producer.python", "status"]
