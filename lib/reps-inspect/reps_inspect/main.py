"""reps-inspect-eval: run an Inspect eval and translate its log into REPS events.

    reps-inspect-eval CONFIG.json

One run = one `inspect_ai.eval(...)` over one task. The adapter merges $REPS_SEED
into the params and hands us this config; we resolve the task, run the eval into a
private log dir, translate the EvalLog (translate.py), and deposit the raw `.eval`
log as an artifact. Execution failures preserve available evidence and return
a nonzero exit code.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

from reps_events import CustomEvent, CapturedLine, Log, Result, Status, emit, emit_producer
from .models import Params, inspect_model
from .sandbox_status import sandbox_provisioning_status
from .translate import (emit_aggregate, emit_provenance,
                        emit_sample, SampleTranscript, drift_warnings)

_ZERO = {"samples": 0, "completed": 0, "errors": 0,
         "score": 0.0, "score_name": "", "tokens_input": 0, "tokens_output": 0}


def resolve_task(spec: str):
    """`pkg:<module>:<attr>` -> the imported @task callable (how a family selects a
    task function from an installed package, e.g. `pkg:impossiblebench:impossible_swebench`);
    anything else (registry id or `path/file.py@task_fn`) passes through to Inspect
    verbatim."""
    if spec.startswith("pkg:"):
        modname, sep, attr = spec[4:].partition(":")
        if not sep or not modname or not attr:
            raise ValueError(f"pkg: task spec must be pkg:<module>:<attr>, got {spec!r}")
        import importlib
        return getattr(importlib.import_module(modname), attr)
    return spec


def eval_kwargs(params: Params, log_dir: Path) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "model": inspect_model(params.model),
        "task_args": params.task_args,
        "model_args": params.model_args,
        "log_dir": str(log_dir),
        "log_format": "eval",
        "display": "none",
        "seed": params.seed,
    }
    if params.limit:
        kw["limit"] = params.limit
    if params.epochs != 1:
        kw["epochs"] = params.epochs
    if params.max_connections:
        kw["max_connections"] = params.max_connections
    if params.message_limit:
        kw["message_limit"] = params.message_limit
    if params.token_limit:
        kw["token_limit"] = params.token_limit
    # generation config (temperature, max_tokens, reasoning_effort, …) as top-level
    # GenerateConfigArgs kwargs; scoped to generation keys by the caller
    kw.update(params.generate_args)
    return kw


def deposit_log(log_obj: Any, run_dir: Path) -> None:  # Any: EvalLog, whose import is deliberately lazy
    """Copy the run's `.eval` log into the deposit as an artifact (the irreducible
    Inspect record; the translated events are its secondary rendering)."""
    src = getattr(log_obj, "location", None)
    if not src or not Path(src).exists():
        return
    dest_dir = run_dir / "artifacts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "run.eval"
    shutil.copyfile(src, dest)
    emit(
        CustomEvent(kind="inspect.artifact", data={
            "name": "run.eval", "path": "artifacts/run.eval",
            "media_type": "application/octet-stream", "bytes": dest.stat().st_size,
        })
    )


class PrintStream(io.TextIOBase):
    """Solver prints, captured at the source and re-emitted as `stdout` events.

    Inspect runs samples concurrently in one process, so raw prints interleave
    unattributably; capturing at print time lets us read inspect's active-sample
    contextvar and tag each line with the sample that printed it. (The runner
    normally synthesizes `stdout` events itself — this emits the same type one
    hop earlier, where the attribution still exists; anything that escapes this
    capture still becomes a runner-synthesized line.) Tagged lines are submitted
    through the event socket."""

    def __init__(self) -> None:
        self._buf = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:  # pragma: no cover - io plumbing
        return True

    def write(self, s: str) -> int:
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    emit(CapturedLine(type="stdout", line=line, **self._sample_tag()))
        return len(s)

    @staticmethod
    def _sample_tag() -> dict[str, dict[str, str | int | None]]:
        try:
            from inspect_ai.log._samples import sample_active  # internal, pinned
            active = sample_active()
            if active is not None:
                # spec instance-convention attribution (docs/book/src/reference/events.md)
                return {"meta": {"instance_id": active.sample.id,
                                 "repeat": active.epoch}}
        except Exception:
            pass
        return {}


def run(params: Params) -> int:
    with drift_warnings():
        return _run(params)


def _run(params: Params) -> int:
    run_dir = Path(__import__("os").environ.get("REPS_RUN_DIR", "."))
    work = Path.cwd()
    log_dir = work / "inspect-logs"

    # imported lazily so a config/param error reports cleanly before the heavy
    # numpy/inspect_ai import chain runs
    # upstream's `eval` signature carries an untyped Scanner param, which strict
    # counts against anyone importing it — their gap, scoped suppression here
    from inspect_ai import eval as run_eval  # pyright: ignore[reportUnknownVariableType]
    from inspect_ai.hooks import Hooks, hooks
    from inspect_ai.log._samples import sample_active  # internal, pinned

    agent = params.model
    streamed: set[str] = set()
    live: dict[str, SampleTranscript] = {}

    # STREAM: run_eval blocks through the whole eval, so emit as it goes — a status
    # when each sample starts, each completed native transcript prefix, and a final
    # reconciliation pass. A raw ModelEvent and its derived call stay adjacent.
    # hook `data` params are Any on purpose: their classes ride the pinned inspect
    # version and this bridge duck-types them (getattr-guarded) rather than binding
    # to one release's names. The decorator itself is untyped upstream.
    @hooks(name="reps-stream", description="stream REPS events as the eval runs")  # pyright: ignore[reportUntypedClassDecorator]
    class RepsStream(Hooks):
        async def on_sample_start(self, data: Any) -> None:
            s = data.summary
            live[data.sample_id] = SampleTranscript(s.id, s.epoch)
            emit(Status(detail=f"instance {s.id} repeat {s.epoch}: running"))

        async def on_sample_event(self, data: Any) -> None:
            st = live.get(data.sample_id)
            active = sample_active()
            if st is None or active is None:
                return
            try:
                st.emit_ready(active.transcript.events, agent)
            except Exception as exc:  # a bad event must not kill the eval
                emit(Log(message=f"stream: live emit failed: {exc}", level="warn"))

        async def on_sample_end(self, data: Any) -> None:
            if data.sample is None:
                return
            st = live.pop(data.sample_id, None)
            try:
                emit_sample(data.sample, agent, transcript=st)
                streamed.add(data.sample.uuid)
            except Exception as exc:  # a bad sample must not kill the eval
                emit(Log(message=f"stream: sample emit failed: {exc}", level="warn"))

    _ = RepsStream  # registered by the decorator's side effect, never referenced

    task = resolve_task(params.task)
    emit(
        Status(detail=f"running inspect eval: task={params.task} model={params.model}")
    )
    # Structured events use the socket; everything the
    # eval prints is captured and re-emitted as tagged `stdout` events (PrintStream),
    # and inspect's otherwise-silent docker provisioning is narrated as status
    # events (sandbox_status.py)
    try:
        with contextlib.redirect_stdout(PrintStream()), sandbox_provisioning_status():
            logs = run_eval(task, **eval_kwargs(params, log_dir))
    except Exception as exc:
        emit(Log(message=f"inspect eval failed to run: {exc}", level="error"))
        raise

    if not logs:
        emit(Log(message="inspect eval produced no log", level="error"))
        raise RuntimeError("inspect eval produced no log")

    log_obj = logs[0]
    emit_provenance(log_obj, agent)
    # fallback: emit any sample the hook didn't stream (hook disabled / raced)
    for sample in log_obj.samples or []:
        if getattr(sample, "uuid", None) not in streamed:
            emit_sample(sample, agent)
    summary = emit_aggregate(log_obj, agent)
    deposit_log(log_obj, run_dir)
    if log_obj.error:
        emit(Log(message=f"eval error: {log_obj.error.message}", level="error"))
    emit(
        Status(
            detail=f"done: {log_obj.status} score={summary['score']} ({summary['completed']}/{summary['samples']} samples)"
        )
    )
    if log_obj.status != "success":
        return 1
    return 0


def _emit_zero() -> None:
    for k, v in _ZERO.items():
        emit(Result(name=k, value=v))
    emit(Status(detail="done: status=error (eval did not run)"))


def main() -> int:
    # the task's own prints (solver progress etc.) must not sit in a block buffer
    # until the next event flush pushes them out — the runner reads this pipe live
    try:
        # duck-typed: reconfigure exists on TextIOWrapper stdouts (a real console
        # or pipe), not on every TextIO stand-in (pytest's capture, StringIO)
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True)
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="reps-inspect-eval", description=__doc__)
    parser.add_argument("config", help="path to a JSON config file")
    args = parser.parse_args()
    try:
        params = Params.model_validate(json.loads(Path(args.config).read_text()))
    except Exception:
        traceback.print_exc()
        return 1
    try:
        emit_producer()
        return run(params)
    except Exception:
        traceback.print_exc()
        _emit_zero()
        return 1


if __name__ == "__main__":
    sys.exit(main())
