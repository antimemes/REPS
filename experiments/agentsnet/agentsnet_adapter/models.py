"""agentsnet schema 0: shared technical events and the typed observations the
adapter emits around the authors' code.

This module imports nothing from the authors' tree or LangChain so the
build-time schema export can import it on its own.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import Field, JsonValue

from reps_events import (ActorRegistry, CapturedLine, CustomEvent, LLMCall, Log, ProducerPython,
                        RenderHint, Result, RunEnd, RunStart, Status)
from reps_events.models.base import Model, NonNegativeInt


class GraphNode(Model):
    id: NonNegativeInt
    name: str


class GraphEdge(Model):
    source: NonNegativeInt
    target: NonNegativeInt


class GraphData(Model):
    """The instance the agents run on, as loaded from the pinned dataset: the
    authors' node-link data plus the covariates their generator recorded."""

    generator: str
    index: NonNegativeInt
    num_nodes: NonNegativeInt
    num_edges: NonNegativeInt
    diameter: NonNegativeInt
    max_degree: NonNegativeInt
    graph_seed: int | None = None      # the generator seed the dataset row carries
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ConfigData(Model):
    params: dict[str, JsonValue]
    seed: int
    provider: str
    served_model: str
    rounds: NonNegativeInt             # upstream determine_rounds, fixed before the first call
    upstream_rev: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None   # of the authors' tree, when the launcher names it
    upstream_class: str                # the task class driven
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MessageData(Model):
    """One message an agent addressed to a neighbour in one round, reconstructed
    from its transcript (the JSON object upstream parsed from its reply)."""

    round: Annotated[int, Field(ge=1)]
    sender: str
    recipient: str
    text: str
    delivered: bool                    # upstream delivers only to neighbours named by the recipient's name


class AnswerData(Model):
    agent: str
    answer: str | None = None          # None (omitted on the wire): unparseable after the re-prompt; upstream scores it as None
    valid: bool                        # among upstream's valid answers for that agent


class UpstreamRecordData(Model):
    """Identity of the results JSON the authors' code wrote."""

    source: str
    bytes: NonNegativeInt
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ArtifactData(Model):
    """The pointer reps_experiment.scaffold.deposit_artifact emits."""

    name: str
    path: str
    media_type: str | None = None
    bytes: NonNegativeInt


class AgentsNetConfig(CustomEvent[ConfigData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="settings", title="{data.upstream_class} · {data.rounds} rounds · {data.served_model}",
        fields=["data.upstream_class", "data.rounds", "data.params", "data.seed", "data.provider",
                "data.served_model", "data.upstream_rev", "data.dataset_sha256"],
    )
    kind: Literal["agentsnet.config"] = "agentsnet.config"


class AgentsNetGraph(CustomEvent[GraphData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="waypoints", title="{data.generator} graph · {data.num_nodes} nodes · {data.num_edges} edges · diameter {data.diameter}",
        body="data", format="json",
    )
    kind: Literal["agentsnet.graph"] = "agentsnet.graph"


class AgentsNetMessage(CustomEvent[MessageData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="message-square", title="round {data.round} · {data.sender} to {data.recipient}",
        body="data.text", badge="data.delivered",
    )
    kind: Literal["agentsnet.message"] = "agentsnet.message"


class AgentsNetAnswer(CustomEvent[AnswerData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="flag", title="{data.agent} answers {data.answer}", badge="data.valid",
        fields=["data.agent", "data.answer", "data.valid"],
    )
    kind: Literal["agentsnet.answer"] = "agentsnet.answer"


class AgentsNetUpstreamRecord(CustomEvent[UpstreamRecordData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="file-input", title="{data.source}", fields=["data.source", "data.bytes", "data.sha256"],
    )
    kind: Literal["agentsnet.upstream_record"] = "agentsnet.upstream_record"


class AgentsNetArtifact(CustomEvent[ArtifactData]):
    render: ClassVar[RenderHint] = RenderHint(
        icon="paperclip", title="{data.name}", fields=["data.name", "data.path", "data.media_type", "data.bytes"],
    )
    kind: Literal["agentsnet.artifact"] = "agentsnet.artifact"


type AgentsNetCustom = Annotated[
    AgentsNetConfig | AgentsNetGraph | AgentsNetMessage | AgentsNetAnswer | AgentsNetUpstreamRecord
    | AgentsNetArtifact,
    Field(discriminator="kind"),
]
type Payload = Annotated[
    RunStart | RunEnd | LLMCall | AgentsNetCustom | Result
    | Status | Log | CapturedLine | ProducerPython,
    Field(discriminator="type"),
]

__all__ = ["Payload", "ActorRegistry"]
