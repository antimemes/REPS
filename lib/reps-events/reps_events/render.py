"""Presentation metadata for schema readers; hints never enter event records.

The former ``group`` hint was removed: viewers render rows in sequence and use
facets to narrow the stream instead of folding records together.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator


_PATH = r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*"
_SUBSTITUTION = re.compile(r"\{(" + _PATH + r")\}")


def template_paths(value: str | None) -> list[str]:
    """A bare path keeps its original meaning; templates substitute only {paths}."""
    if value is None:
        return []
    if re.fullmatch(_PATH, value):
        return [value]
    return _SUBSTITUTION.findall(value)


class ActorRegistry(BaseModel):
    """A payload map from actor IDs to entries with a display-label field."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str
    label: str


class RenderHint(BaseModel):
    """Payload paths and literal text with {path} substitutions, never expressions.

    A title/body consisting solely of a bare field path remains a path lookup.
    Missing substitutions are empty. Fields and badge always contain bare paths.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    icon: str
    actor: str | None = None
    actor_label: str | None = None
    actor_registry: ActorRegistry | None = None
    title: str | None = None
    body: str | None = None
    format: Literal["text", "markdown", "json", "html-text"] = "text"
    fields: list[str] | None = None
    badge: str | None = None

    @field_validator("title", "body")
    @classmethod
    def simple_template(cls, value: str | None) -> str | None:
        if value is not None and any(char in _SUBSTITUTION.sub("", value) for char in "{}"):
            raise ValueError("render templates allow only {dotted.path} substitutions")
        return value


def hint_paths(hint: RenderHint) -> list[str]:
    """Paths used to validate typed declarations and native-row fixtures."""
    return [path for path in (hint.actor, hint.actor_label, hint.badge) if path] + [
        *template_paths(hint.title), *template_paths(hint.body), *(hint.fields or []),
    ]


def export_schema(union: Any) -> dict[str, Any]:
    """Export a payload union, including class render hints on its definitions.

    Standard JSON Schema if/then clauses carry hints that depend on a literal
    field (log severity and captured output channel). They do not constrain data.
    """
    supplied: TypeAdapter[Any] = union
    adapter = supplied if isinstance(union, TypeAdapter) else TypeAdapter[Any](union)
    return adapter.json_schema(by_alias=True)


def hint_path_declared(schema: dict[str, Any], path: str, root: dict[str, Any]) -> bool:
    """Every path segment must resolve to a declared JSON Schema property."""
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            return any(hint_path_declared(member, path, root) for member in schema[union_key])
    key, dot, rest = path.partition(".")
    field = schema.get("properties", {}).get(key)
    if field is None:
        return False
    return not dot or hint_path_declared(field, rest, root)
