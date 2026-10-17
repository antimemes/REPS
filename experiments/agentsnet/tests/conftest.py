"""Tests run the authors' code for real with the mock model, in-process, and
the reps-testing `event_capture` receiver collects each event. Each test gets
a run directory; the adapter copies the authors' tree that AGENTSNET_UPSTREAM
names (tests/default.nix sets it to the pinned repository) into it and reads the
dataset that AGENTSNET_DATASET names.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def run_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("REPS_SEED", "7")
    for var in ("AGENTSNET_UPSTREAM", "AGENTSNET_DATASET"):
        if not os.environ.get(var):
            pytest.fail(f"{var} must be set (the authors' tree / the dataset parquet); tests/default.nix sets both")
    cwd = os.getcwd()
    yield tmp_path
    os.chdir(cwd)
    # upstream modules keep module-level state (patched attributes); drop them,
    # thus the next test imports them again against its run directory
    for name in ("main", "LiteralMessagePassing", "utils", "generate_graphs", "chat_tool"):
        sys.modules.pop(name, None)
    for entry in [p for p in sys.path if str(tmp_path) in p]:
        sys.path.remove(entry)


def events_of(collected, type_: str, kind: str | None = None) -> list[dict]:
    return [e for e in collected if e.get("type") == type_ and (kind is None or e.get("kind") == kind)]


def results_of(collected) -> dict:
    return {e["name"]: e["value"] for e in events_of(collected, "result")}
