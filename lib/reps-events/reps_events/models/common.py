"""General producer events and experiment-specific data."""

from typing import Any, ClassVar, Literal

from pydantic import JsonValue

from .base import Event, Scalar
from ..render import RenderHint


class ProducerPython(Event[Literal["producer.python"]]):
    """The producing interpreter's toolchain, emitted once before its observations."""

    render: ClassVar[RenderHint] = RenderHint(icon="code", body="{implementation} {version}")
    type: Literal["producer.python"] = "producer.python"
    implementation: str
    version: str
    executable: str | None = None
    platform: str
    libc: str | None = None
    locale: str | None = None
    hash_seed: int | None = None
    flags: list[str]


class Status(Event[Literal["status"]]):
    render: ClassVar[RenderHint] = RenderHint(icon="info", body="detail")
    type: Literal["status"] = "status"
    detail: str


class Log(Event[Literal["log"]]):
    render: ClassVar[RenderHint] = RenderHint(icon="info", body="message")
    render_variants = {"level": {
        level: RenderHint(icon=icon, body="message")
        for level, icon in {"debug": "bug", "info": "info", "warn": "triangle-alert", "error": "circle-x"}.items()
    }}
    type: Literal["log"] = "log"
    message: str
    level: Literal["debug", "info", "warn", "error"] = "info"


class CapturedLine(Event[Literal["stdout", "stderr"]]):
    render: ClassVar[RenderHint] = RenderHint(icon="terminal", body="line")
    render_variants = {"type": {
        channel: RenderHint(icon="terminal", body="line")
        for channel in ("stdout", "stderr")
    }}
    type: Literal["stdout", "stderr"]
    line: str
    meta: dict[str, Any] | None = None


class Result(Event[Literal["result"]]):
    """An experiment reports a result declared in its manifest."""

    render: ClassVar[RenderHint] = RenderHint(icon="chart-no-axes-combined", title="name", body="value")

    type: Literal["result"] = "result"
    name: str
    value: Scalar


class CustomEvent[Data = dict[str, JsonValue]](Event[Literal["custom"]]):
    """Experiment-specific observations within the public wire vocabulary."""

    type: Literal["custom"] = "custom"
    kind: str
    data: Data
