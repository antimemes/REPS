"""EvalLog -> REPS event stream (docs/book/src/reference/events.md).

Every native transcript event is retained as an inspect.event custom record.
Model events additionally derive llm.call immediately after their raw record;
inspect.sample carries outcomes and result events carry aggregate results.

Inspect telemetry is secondary data (transcript-derived), so callers may weigh
it accordingly — same posture as the harness normalizers in specs/harness.md.

Annotations use the real pinned inspect types (TYPE_CHECKING only — importing
inspect_ai at module scope would defeat main.py's lazy heavy-import posture);
guards below mirror what those types actually promise: `output` and `eval` are
required fields, so the empty-response case is `not output.choices`, never None.
"""

from __future__ import annotations

import re

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from reps_events import (
    LLMCall,
    Log,
    CustomEvent,
    Result,
    Status,
    emit,
)
from reps_events.inspect_chat import (
    ChatCompletionChoice, ChatMessageBase, ContentToolUse, ToolCall,
)

if TYPE_CHECKING:
    from inspect_ai.event import Event as InspectEvent, ModelEvent
    from inspect_ai.log import EvalLog, EvalSample
    from inspect_ai.scorer import Value

# Inspect Score.value uses these letter grades; map to 0/1 for numeric metrics.
_GRADE = {"C": 1.0, "I": 0.0, "P": 0.5, "N": 0.0}

# Deliberate runtime/file-format omissions are not schema drift.
_MODEL_EVENT_EXCLUDE: dict[str, Any] = {
    name: True for name in (
        "event", "timestamp", "uuid", "span_id", "pending", "working_start",
        "input_refs", "traceback", "traceback_ansi", "config", "role", "retries", "cache",
    )
}
_MODEL_EVENT_EXCLUDE["call"] = {"call_refs", "call_key"}
_DRIFT: ContextVar[set[str] | None] = ContextVar("inspect_dropped_fields", default=None)
_OMIT = object()


def _warn_drift(paths: set[str]) -> None:
    if paths:
        emit(Log(level="warn", message="Inspect schema drift: dropped unknown fields: "
                 + ", ".join(sorted(paths))))


@contextmanager
def drift_warnings() -> Generator[None]:
    """Collect dropped paths across all samples and warn once when the run ends."""
    if _DRIFT.get() is not None:
        yield
        return
    paths: set[str] = set()
    token = _DRIFT.set(paths)
    try:
        yield
    finally:
        _DRIFT.reset(token)
        _warn_drift(paths)


def _branches(annotation: Any) -> list[Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _branches(args[0])
    if origin in (Union, UnionType):
        return [branch for arg in args for branch in _branches(arg)]
    return [annotation]


def _literals(annotation: Any) -> set[Any]:
    return {value for branch in _branches(annotation)
            if get_origin(branch) is Literal for value in get_args(branch)}


def _filter_fields(
    value: Any, annotation: Any, path: str, dropped: set[str],
    originals: dict[str, dict[str, Any]],
) -> Any:
    """Walk declared models; leave open provider/metadata/JSON-schema dicts intact."""
    branches = _branches(annotation)
    original: Any = value
    items: list[Any] = original
    data: dict[str, Any] = original
    if isinstance(value, list):
        item_type = next((get_args(b)[0] for b in branches if get_origin(b) is list), None)
        if item_type is not None:
            filtered = [_filter_fields(item, item_type, f"{path}[{i}]", dropped, originals)
                        for i, item in enumerate(items)]
            return [item for item in filtered if item is not _OMIT]
    if not isinstance(value, dict):
        return original
    models: list[type[BaseModel]] = [
        b for b in branches if isinstance(b, type) and issubclass(b, BaseModel)
    ]
    if len(models) > 1:
        # Vendored unions discriminate chat messages by role and content/citations
        # by type. Preserve new upstream variants without losing the whole call.
        discriminator = next((key for key in ("role", "type") if all(
            key in model.model_fields and _literals(model.model_fields[key].annotation)
            for model in models
        )), None)
        if discriminator is not None:
            tag = data.get(discriminator)
            models = [model for model in models
                      if tag in _literals(model.model_fields[discriminator].annotation)]
            if not models and isinstance(tag, str):
                originals.setdefault("original_content", {})[path] = data
                return _OMIT
    if len(models) != 1:
        return original
    model = models[0]
    result: dict[str, Any] = {}
    for name, child in data.items():
        child_path = f"{path}.{name}" if path else name
        field = model.model_fields.get(name)
        if field is None:
            if (issubclass(model, ChatMessageBase) and name == "source"
                    or model is ToolCall and name == "view"):
                continue  # Harness/viewer data, deliberately excluded from the boundary.
            # Collapse list indices so the run summary grows with schema changes,
            # not with the number of messages or model calls.
            dropped.add(re.sub(r"\[\d+\]", "[]", child_path))
            continue
        normalize = (
            (model is ChatCompletionChoice and name == "stop_reason")
            or (model is ContentToolUse and name == "tool_type")
        )
        if normalize and child is not None and child not in _literals(field.annotation):
            originals.setdefault(f"original_{name}", {})[child_path] = child
            if "unknown" in _literals(field.annotation):
                child = "unknown"
            elif not field.is_required():
                continue
            else:
                # ContentToolUse.tool_type is required and has no unknown value.
                # Preserve the entire unrepresentable block in record metadata.
                originals.setdefault("original_content", {})[path] = data
                return _OMIT
        result[name] = _filter_fields(child, field.annotation, child_path, dropped, originals)
    return result


def _num(value: Value) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _GRADE.get(value.strip().upper())
    return None


def emit_provenance(log: EvalLog, agent: str) -> None:
    """Record available package/task versions and dataset/source metadata.

    Dataset name, location, and sample count are not a content fingerprint.
    """
    e = log.eval
    ds = e.dataset
    rev = e.revision
    emit(
        CustomEvent(
            kind="inspect.provenance",
            data={
                "agent": agent,
                "packages": {key: value for key, value in (e.packages or {}).items()},
                "task": e.task,
                "task_version": e.task_version,
                "task_registry_name": e.task_registry_name,
                "model": e.model,
                "dataset": {
                    "name": ds.name,
                    "location": ds.location,
                    "samples": ds.samples,
                },
                "revision": (
                    {"origin": rev.origin, "commit": rev.commit, "dirty": rev.dirty}
                    if rev is not None
                    else None
                ),
            },
        )
    )


def emit_model_event(ev: ModelEvent, agent: str,
                     sample_id: str | int, epoch: int) -> None:
    """Derive boundary data; harness config and bookkeeping stay in the raw event."""
    payload = {
        **ev.model_dump(mode="json", exclude_none=True, exclude=_MODEL_EVENT_EXCLUDE),
        "agent": ev.role if ev.role is not None else agent,
    }
    metadata: dict[str, Any] = {"inspect.sample_id": sample_id, "inspect.epoch": epoch}
    if native_metadata := payload.pop("metadata", None):
        metadata["inspect.metadata"] = native_metadata
    if ev.cache is not None:
        metadata["inspect.cache"] = ev.cache
    payload["metadata"] = metadata
    dropped: set[str] = set()
    originals: dict[str, dict[str, Any]] = {}
    filtered: dict[str, Any] = _filter_fields(payload, LLMCall, "", dropped, originals)
    if originals:
        metadata = dict(filtered.get("metadata") or {})
        for key, values in originals.items():
            # A single value uses the simple original_<field> shape. Repeated
            # fields across choices/messages retain their precise source paths.
            original = (next(iter(values.values()))
                        if len(values) == 1 and key != "original_content" else values)
            metadata[f"inspect.{key}"] = original
        filtered["metadata"] = metadata
    paths = _DRIFT.get()
    if paths is not None:
        paths.update(dropped)
    emit(LLMCall.model_validate(filtered))
    if paths is None:
        _warn_drift(dropped)  # Keep the derived call adjacent to its raw record.


class SampleTranscript:
    """Emit each sample's completed transcript prefix in its native order.

    Inspect delivers hooks when events complete, which may differ from transcript
    order under nesting. Wait behind pending events, then flush the whole prefix.
    The final pass includes unfinished events and reconciles anything hooks missed.
    """

    def __init__(self, sample_id: str | int, epoch: int) -> None:
        self.sample_id = sample_id
        self.epoch = epoch
        self.position = 0

    def emit_ready(self, events: Sequence[InspectEvent], agent: str, *, final: bool = False) -> None:
        from inspect_ai.event import ModelEvent

        while self.position < len(events):
            ev = events[self.position]
            if ev.pending and not final:
                break
            emit(CustomEvent(kind="inspect.event", data=ev.model_dump(mode="json", exclude_none=True)))
            if isinstance(ev, ModelEvent):
                emit_model_event(ev, agent, self.sample_id, self.epoch)
            self.position += 1


_SCALAR = (int, float, str, bool)


def _flat_scores(scores: Mapping[str, Value]) -> dict[str, int | float | str | bool]:
    """Inspect Score.value per scorer → the spec's flat scalar map: dict-valued
    scorers (agentharm's combined_scorer) flatten with '/'-joined names; a
    non-scalar leaf stringifies (degraded-but-correct — the raw .eval artifact
    keeps the original)."""
    out: dict[str, int | float | str | bool] = {}
    for scorer, v in scores.items():
        if isinstance(v, Mapping):
            for k, leaf in v.items():
                out[f"{scorer}/{k}"] = leaf if isinstance(leaf, _SCALAR) else str(leaf)
        else:
            out[scorer] = v if isinstance(v, _SCALAR) else str(v)
    return out


def emit_sample(sample: EvalSample, agent: str, *,
                transcript: SampleTranscript | None = None) -> None:
    """Reconcile all native events, then emit the sample's outcome."""
    transcript = transcript or SampleTranscript(sample.id, sample.epoch)
    transcript.emit_ready(sample.events, agent, final=True)

    emit(
        CustomEvent(
            kind="inspect.sample",
            data={
                "agent": agent,
                "id": sample.id,
                "repeat": sample.epoch,
                "target": ([value for value in sample.target]
                           if isinstance(sample.target, list) else sample.target),
                "scores": {key: value for key, value in _flat_scores(
                    {k: v.value for k, v in (sample.scores or {}).items()}
                ).items()},
                "error": sample.error.message if sample.error else None,
            },
        )
    )


def headline(log: EvalLog) -> tuple[float, str]:
    """Pick the run's single reportable score: an `accuracy`-named aggregate result
    if present, else the first numeric aggregate result. Returns (value, name)."""
    results = log.results
    if not results:
        return 0.0, ""
    first: tuple[float, str] | None = None
    for score in results.scores:
        for name, m in score.metrics.items():
            num = _num(m.value)
            if num is None:
                continue
            qualified = f"{score.name}/{name}"
            if name == "accuracy":
                return num, qualified
            if first is None:
                first = (num, qualified)
    return first if first is not None else (0.0, "")


def emit_aggregate(log: EvalLog, agent: str) -> dict[str, Any]:
    """The run-level tail: aggregate scorer metrics, token totals, and the scalar
    results summary. Emitted once after the eval; model calls and inspect.event
    and inspect.sample custom events stream separately as each sample completes."""
    results = log.results
    if results:
        for score in results.scores:
            for name, m in score.metrics.items():
                num = _num(m.value)
                if num is not None:
                    emit(CustomEvent(kind="inspect.metric", data={
                        "name": f"{score.name}/{name}", "value": num,
                    }))

    ti = to = 0
    for usage in (log.stats.model_usage or {}).values():
        ti += usage.input_tokens or 0
        to += usage.output_tokens or 0

    total = results.total_samples if results else 0
    completed = results.completed_samples if results else 0
    errors = sum(1 for s in (log.samples or []) if s.error)
    score, score_name = headline(log)

    summary: dict[str, Any] = {
        "samples": total, "completed": completed,
        "errors": errors, "score": score, "score_name": score_name,
        "tokens_input": ti, "tokens_output": to,
    }
    for k, v in summary.items():
        emit(Result(name=k, value=v))
    return summary


def emit_all(log: EvalLog, agent: str) -> dict[str, Any]:
    """Translate the whole EvalLog at once (the batch path — used by tests and any
    caller that has a finished log). main.py streams instead: samples via a hook,
    then emit_aggregate."""
    with drift_warnings():
        emit(Status(detail=f"eval {log.status}: {log.eval.task} on {agent}"))
        emit_provenance(log, agent)
        for sample in log.samples or []:
            emit_sample(sample, agent)
        return emit_aggregate(log, agent)
