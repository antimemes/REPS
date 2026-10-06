"""Public event models, discriminated unions, and schema registries."""

from collections.abc import Mapping
import json
from typing import Annotated, Any, get_args

from pydantic import ConfigDict, Field, TypeAdapter
from ..render import RenderHint
from ..identity import RunId
from ..version import VOCABULARY_VERSION, VocabularyVersion

from .base import Event, Model, NonNegativeInt, UtcDatetime
from .base import Json as Json, Scalar as Scalar
from .common import (
    ProducerPython as ProducerPython,
    Status as Status,
    Log as Log,
    CapturedLine as CapturedLine,
    Result as Result,
    CustomEvent as CustomEvent,
)
from .llm import (
    LLMCall as LLMCall,
)
from .run import (
    RunEnvironment as RunEnvironment,
    RunStart as RunStart,
    RunEnd as RunEnd,
)


# Presentation lives outside the LLM wire models.
LLMCall.render = RenderHint(icon="sparkles", actor="agent")

# These unions are the authority. The registry used for per-type schema export
# is derived from their literal tags, rather than maintaining a second list.
type ProducerPayload = Annotated[
    ProducerPython
    | Status
    | Log
    | CapturedLine
    | Result
    | LLMCall
    | CustomEvent,
    Field(discriminator="type"),
]
type Payload = Annotated[
    ProducerPayload | RunStart | RunEnd,
    Field(discriminator="type"),
]
PRODUCER_ADAPTER = TypeAdapter[ProducerPayload](ProducerPayload)
EVENT_ADAPTER = TypeAdapter[Payload](Payload)


def _event_models(annotation: Any) -> dict[str, type[Event[Any]]]:
    from typing import TypeAliasType

    if isinstance(annotation, TypeAliasType):
        return _event_models(annotation.__value__)
    if isinstance(annotation, type) and issubclass(annotation, Event):
        return {
            tag: annotation
            for tag in get_args(annotation.model_fields["type"].annotation)
        }
    models: dict[str, type[Event[Any]]] = {}
    for arg in get_args(annotation):
        models.update(_event_models(arg))
    return models


EVENT_MODELS = _event_models(Payload)
PRODUCER_MODELS = _event_models(ProducerPayload)


class Envelope[EventPayload = Payload](Model):
    """A current-vocabulary record with a conformant payload."""

    model_config = ConfigDict(serialize_by_alias=True)

    v: VocabularyVersion = VOCABULARY_VERSION
    ts: UtcDatetime
    run: RunId
    experiment: str
    # BaseModel.schema is a deprecated method; keep the wire name via an alias.
    schema_: NonNegativeInt = Field(alias="schema")
    seq: NonNegativeInt
    event: EventPayload


def validate_event(payload: Mapping[str, Any]) -> list[str]:
    """Validate the public vocabulary without modifying the original payload."""
    try:
        EVENT_ADAPTER.validate_json(json.dumps(dict(payload), allow_nan=False), strict=True)
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    return []
