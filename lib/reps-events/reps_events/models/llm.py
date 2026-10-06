"""Model API boundary data in the REPS event vocabulary.

Based on Inspect AI 0.3.263, src/inspect_ai/event/_model.py and _base.py.
Nested vendored models and full provenance: ../inspect_chat.py.

Local changes: retain boundary fields; add agent attribution and an optional
non-negative retries count for observed HTTP 429/5xx responses. The retries property
falls back to the legacy reps_experiment.retries metadata note on old records;
the legacy reps_experiment.backend marker establishes zero when no count exists.
Inspect's runtime retry bookkeeping remains in its raw events.
Drop role, cache, config and its params/temperature/max_tokens projections.
Drop deferred REPS instance_id/repeat attribution. Generation settings stay in call.request.
Drop input_refs, traceback/traceback_ansi, pending, working_start, span_id, and
runtime helpers. The envelope owns identity and timestamp; type is llm.call.
"""

from typing import Any, Literal

from pydantic import Field, NonNegativeInt

from ..inspect_chat import ChatMessage, ModelCall, ModelOutput, ToolChoice, ToolInfo
from .base import Event, UtcDatetime


class LLMCall(Event[Literal["llm.call"]]):
    type: Literal["llm.call"] = "llm.call"
    model: str
    input: list[ChatMessage]
    tools: list[ToolInfo] = Field(default_factory=list[ToolInfo])
    tool_choice: ToolChoice = "auto"
    output: ModelOutput
    call: ModelCall | None = None
    error: str | None = None
    completed: UtcDatetime | None = None
    working_time: float | None = None
    metadata: dict[str, Any] | None = None
    agent: str | None = None

    retries_: NonNegativeInt | None = Field(
        default=None,
        alias="retries",
        description="Observed HTTP 429/5xx responses, including the final rejection on exhaustion; zero when none, absent when unknown.",
    )

    @property
    def retries(self) -> int | None:
        """Prefer the wire count; legacy client markers distinguish zero from unknown."""
        if self.retries_ is not None:
            return self.retries_
        meta = self.metadata or {}
        if "reps_experiment.retries" in meta:
            return int(meta["reps_experiment.retries"])
        if "reps_experiment.backend" in meta:
            return 0
        return None

    @retries.setter
    def retries(self, value: int | None) -> None:
        self.retries_ = value
