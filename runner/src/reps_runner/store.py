"""Run directory persistence (docs/book/src/reference/layout.md).

runs/<condition_id>-<experiment>/<run_id>/{run.json, events.jsonl, workspace/}

The stream is evidence; run.json is a replaceable runner index card.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from reps_events.identity import RunId


def condition_name(condition: str, experiment: str) -> str:
    """A storage name, derived from record fields; never decoded by splitting it."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", condition) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", experiment):
        raise ValueError("invalid condition or experiment path component")
    return f"{condition}-{experiment}"


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    """Select the shared run store: explicit flag, environment, then XDG default."""
    if data_dir is not None:
        return Path(data_dir).resolve()
    if "REPS_DATA_DIR" in os.environ:
        return Path(os.environ["REPS_DATA_DIR"]).resolve()
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return (Path(xdg) / "reps").resolve()


def write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def find_run(home: Path, run_id: str) -> Path | None:
    """Locate a run directory by its whole ID. Run ids are globally unique but stored under
    their condition, so the condition segment is globbed."""
    matches = sorted((home / "runs").glob(f"*/{run_id}"))
    return matches[0] if matches else None


class RunStore:
    def __init__(self, home: Path, condition_id: str, run_id: str, *, experiment: str):
        self.run_id = TypeAdapter[RunId](RunId).validate_python(run_id, strict=True)
        self.dir = home / "runs" / condition_name(condition_id, experiment) / run_id
        (self.dir / "workspace").mkdir(parents=True, exist_ok=True)
        self._fh = None

    @property
    def workspace(self) -> Path:
        return self.dir / "workspace"

    def write_event(self, event: dict[str, Any]) -> str:
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        if self._fh is None:
            self._fh = (self.dir / "events.jsonl").open("a")
        self._fh.write(line + "\n")
        self._fh.flush()
        return line

    def write_run_json(self, obj: dict[str, Any]) -> None:
        write_json_atomic(self.dir / "run.json", obj)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
