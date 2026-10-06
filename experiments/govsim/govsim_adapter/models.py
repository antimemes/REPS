"""GovSim schema 0: shared technical events and typed simulation observations."""

from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, JsonValue, RootModel

from reps_events import ActorRegistry, CapturedLine, CustomEvent, RenderHint, LLMCall, Log, ProducerPython, Result, RunEnd, RunStart, Status
from reps_events.models.base import Model, NonNegativeInt


class ConfigData(Model):
    experiment: dict[str, JsonValue]
    code_version: str
    group_name: str
    llm: dict[str, JsonValue]
    embedder: str | None = None
    embedder_revision: str | None = None
    threads: int | None = None
    mix_llm: list[JsonValue]
    seed: int
    debug: bool


class StateData(Model):
    round: int
    resource: int | float | None = None
    collected: dict[str, int | float]
    limit: int | float | None = None
    final: bool


class UnparsedLogData(Model):
    text: str
    error: str | None = None


class UpstreamLogData(Model):
    source: str
    bytes: NonNegativeInt
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    records: NonNegativeInt


class RecordData(RootModel[dict[str, JsonValue]]):
    """An open native row: upstream keys vary by action and are not stable.

    Retain every field and value, including unfamiliar actions and nested data.
    """

    model_config = ConfigDict(strict=True, allow_inf_nan=False)


class MemoryData(Model):
    persona: str
    node: RecordData


class GovsimConfig(CustomEvent[ConfigData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="settings", title="{data.code_version} · {data.group_name}",
        fields=["data.experiment", "data.llm", "data.embedder", "data.mix_llm", "data.seed", "data.debug"],
        actor_registry=ActorRegistry(path="data.experiment.personas", label="name"),
    )
    kind: Literal["govsim.config"] = "govsim.config"


class GovsimState(CustomEvent[StateData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="users", title="round {data.round} · pool {data.resource} · limit {data.limit}",
        badge="data.final", fields=["data.round", "data.resource", "data.limit", "data.collected", "data.final"],
    )
    kind: Literal["govsim.state"] = "govsim.state"


class GovsimUnparsedLog(CustomEvent[UnparsedLogData]):
    render: ClassVar[RenderHint] = RenderHint(icon="file-warning", body="data.text")
    kind: Literal["govsim.unparsed_log"] = "govsim.unparsed_log"


class GovsimUpstreamLog(CustomEvent[UpstreamLogData]):
    """Original file identity, emitted before its ingested native rows."""

    render: ClassVar[RenderHint] = RenderHint(
        icon="file-input", title="{data.source} · {data.records} records",
        fields=["data.source", "data.bytes", "data.sha256", "data.records"],
    )
    kind: Literal["govsim.upstream_log"] = "govsim.upstream_log"


class GovsimMemory(CustomEvent[MemoryData]):
    """A persona's persisted memory node, retaining every native field."""

    render: ClassVar[RenderHint] = RenderHint(
        icon="brain", actor="data.persona", title="{data.node.type}", body="data.node.description",
        fields=["data.node.importance_score", "data.node.created", "data.node.expiration",
                "data.node.subject", "data.node.predicate", "data.node.object"],
    )
    kind: Literal["govsim.memory"] = "govsim.memory"


class GovsimRecord(CustomEvent[RecordData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="file-json", actor="data.agent_id", actor_label="data.agent_name",
        body="data", format="json",
    )
    kind: Literal["govsim.record"] = "govsim.record"


# Actions in GovSim 1d11adf, concurrent_env.py's log_env.json writers.
# Native rows remain open and untouched; these classes identify their action.
_ROW_FIELDS = [f"data.{name}" for name in (
    "agent_id", "round", "action", "resource_in_pool_before_harvesting",
    "resource_in_pool_after_harvesting", "concurrent_harvesting", "resource_collected",
    "wanted_resource", "html_interactions", "agent_name", "resource_limit", "utterance",
)]


class GovsimUtterance(CustomEvent[RecordData]):
    """The Mayor is templated framework output with no model call behind it."""

    kind: Literal["govsim.utterance"] = "govsim.utterance"
    render: ClassVar[RenderHint] = RenderHint(
        icon="message-circle", actor="data.agent_id", actor_label="data.agent_name", title="data.agent_name",
        body="data.utterance", format="markdown",
    )


class GovsimHarvest(CustomEvent[RecordData]):
    kind: Literal["govsim.harvest"] = "govsim.harvest"
    render: ClassVar[RenderHint] = RenderHint(
        icon="fish", actor="data.agent_id", actor_label="data.agent_name",
        title="wanted {data.wanted_resource} → caught {data.resource_collected} · pool "
              "{data.resource_in_pool_before_harvesting} → {data.resource_in_pool_after_harvesting}",
        fields=_ROW_FIELDS,
    )


class GovsimSummary(CustomEvent[RecordData]):
    """Upstream retains this interaction as HTML, not a separate summary string."""

    kind: Literal["govsim.summary"] = "govsim.summary"
    render: ClassVar[RenderHint] = RenderHint(
        icon="notebook-text", actor="data.agent_id", actor_label="data.agent_name",
        body="data.html_interactions", format="html-text",
    )


class GovsimResourceLimit(CustomEvent[RecordData]):
    kind: Literal["govsim.resource_limit"] = "govsim.resource_limit"
    render: ClassVar[RenderHint] = RenderHint(
        icon="gauge", actor="data.agent_id", actor_label="data.agent_name",
        title="limit {data.resource_limit}", fields=_ROW_FIELDS,
    )


ACTION_MODELS = {
    "harvesting": GovsimHarvest,
    "utterance": GovsimUtterance,
    "conversation_summary": GovsimSummary,
    "conversation_resource_limit": GovsimResourceLimit,
}


type GovsimCustom = Annotated[
    GovsimConfig | GovsimState | GovsimUnparsedLog | GovsimUpstreamLog
    | GovsimMemory | GovsimRecord
    | GovsimUtterance | GovsimHarvest | GovsimSummary | GovsimResourceLimit,
    Field(discriminator="kind"),
]
type Payload = Annotated[
    RunStart | RunEnd | LLMCall | GovsimCustom | Result
    | Status | Log | CapturedLine | ProducerPython,
    Field(discriminator="type"),
]
