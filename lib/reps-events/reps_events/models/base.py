"""Shared strict model configuration and event base (no payload imports)."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar, TypeAlias

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer
from pydantic import GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from ..render import RenderHint


def _before_validate_utc_datetime(value: Any) -> Any:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(timezone.utc)
    return value


def serialize_utc_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# UTC normalization adapted from Inspect AI 0.3.263 _util/dateutil.py;
# provenance and MIT license are in ../inspect_chat.py. REPS owns the wire format.
UtcDatetime: TypeAlias = Annotated[
    AwareDatetime,
    BeforeValidator(_before_validate_utc_datetime),
    PlainSerializer(serialize_utc_datetime, return_type=str, when_used="json"),
]


class Model(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class Event[EventType: str = str](Model):
    """Internal base for the closed set of public payload models."""

    type: EventType
    render: ClassVar[RenderHint]
    # Literal-dependent presentation, exported as standard if/then clauses.
    render_variants: ClassVar[dict[str, dict[str, RenderHint]]] = {}

    @classmethod
    def __get_pydantic_json_schema__(cls, core: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        schema = handler.resolve_ref_schema(handler(core))
        hint: RenderHint | None = getattr(cls, "render", None)
        if hint is not None:
            schema["x-reps-render"] = hint.model_dump(mode="json", exclude_none=True)
        for field, variants in cls.render_variants.items():
            for value, hint in variants.items():
                schema.setdefault("allOf", []).append({
                    "if": {"properties": {field: {"const": value}}, "required": [field]},
                    "then": {"x-reps-render": hint.model_dump(mode="json", exclude_none=True)},
                })
        return schema


# Covariant containers for shared API annotations. Open wire objects use
# dict[str, Any] because adapters pass provider-owned JSON with arbitrary shapes.
type Json = Mapping[str, "Json"] | Sequence["Json"] | str | int | float | bool | None
type Scalar = int | float | str | bool
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeNumber = Annotated[int | float, Field(ge=0)]
