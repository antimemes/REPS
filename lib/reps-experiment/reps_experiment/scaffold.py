"""The runner-protocol scaffold for Python experiments.

The protocol (runner side: reps_runner/protocol.py): realized params arrive as
JSON on stdin, ``REPS_RUN_DIR``/``REPS_SEED`` ride in the env, events leave as
JSON over the event socket, and execution failures return a nonzero exit code after
emitting diagnostic evidence and any fallback summary. A config file named on
argv overrides stdin — the hand-run/debug path, and the seam an
adapter uses when it reshapes params first (concordia). Every experiment
repeats that scaffold verbatim, so it lives here:

    def main() -> int:
        return experiment_main(Params, run, prog="my-experiment",
                               fallback_summary={"samples": 0})

Also here: ``deposit_artifact()`` writes a file into the run's ``artifacts/``
and emits its pointer event. Framework stdout is captured by the runner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from reps_events import CustomEvent, Result, emit, emit_producer


class SupportsModelValidate(Protocol):
    """The one method the scaffold needs from a params model CLASS — pydantic's
    ``model_validate`` shape, without depending on (or requiring) pydantic."""

    def model_validate(self, raw: Any, /) -> Any: ...


def experiment_main(params_model: SupportsModelValidate,
                    run: Callable[[Any], object], *, prog: str,
                    description: str | None = None,
                    fallback_summary: dict[str, Any] | None = None,
                    argv: list[str] | None = None) -> int:
    """The whole main(): read params (stdin, or a config file named on argv),
    validate them (any object with a pydantic-style ``model_validate``), default
    ``REPS_RUN_DIR``, call ``run(params)``. If `run` raises, the traceback goes
    to stderr, each entry of `fallback_summary` is emitted as a result, and
    the exit code is 1. Successful execution returns 0."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "config", nargs="?",
        help="path to a JSON (or YAML) config file; omitted = params JSON on stdin (the runner protocol)",
    )
    args = parser.parse_args(argv)
    try:
        raw = (json.load(sys.stdin) if args.config is None
               else json.loads(Path(args.config).read_text()))
        params = params_model.model_validate(raw)
    except Exception:
        traceback.print_exc()
        return 1
    os.environ.setdefault("REPS_RUN_DIR", ".")
    try:
        emit_producer()
        run(params)
    except Exception:
        traceback.print_exc()
        for name, value in (fallback_summary or {}).items():
            emit(Result(name=name, value=value))
        return 1
    return 0


def deposit_artifact(name: str, text: str, *, filename: str, kind: str,
                     media_type: str | None = None) -> Path:
    """Write `text` into a producer-created ``artifacts/`` directory and emit
    a caller-namespaced custom event pointing at it (run-dir-relative path). Returns the
    written path."""
    art_dir = Path(os.environ.get("REPS_RUN_DIR", ".")) / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    path = art_dir / filename
    path.write_text(text, encoding="utf-8")
    emit(
        CustomEvent(kind=kind, data={
            "name": name, "path": f"artifacts/{filename}",
            "media_type": media_type, "bytes": path.stat().st_size,
        })
    )
    return path
