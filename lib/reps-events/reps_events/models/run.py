"""Runner-owned process and launch facts."""

from typing import Any, ClassVar, Literal

from pydantic import Field

from .base import Event, Model, NonNegativeInt, NonNegativeNumber
from ..render import RenderHint


class RunEnvironment(Model):
    platform: str
    # Absent on older records; new runners record the available hardware.
    cpu_model: str | None = None
    cpu_count: int | None = None
    experiment_bin: str | None = None
    runner_python_version: str
    # Supplied by the Nix launcher; absent for direct, unpackaged runner calls.
    runner_bin: str | None = None
    endpoints: dict[str, str] = Field(default_factory=dict)


class RunStart(Event[Literal["run.start"]]):
    """A launch snapshot: identity, inputs, seed and provenance.

    Written before starting the child; it does not assert successful execution.
    Experiment identity belongs to the envelope on every record.
    """

    render: ClassVar[RenderHint] = RenderHint(icon="play")
    type: Literal["run.start"] = "run.start"
    condition: str
    source: str
    fetch_ref: str | None = None
    tree_hash: str | None = None
    params: dict[str, Any]
    seed: NonNegativeInt
    runtime: RunEnvironment
    result_definitions: list[dict[str, Any]]


class RunEnd(Event[Literal["run.end"]]):
    render: ClassVar[RenderHint] = RenderHint(icon="flag")
    type: Literal["run.end"] = "run.end"
    state: Literal["completed", "failed", "interrupted"]
    duration_s: NonNegativeNumber
    exit_code: int
