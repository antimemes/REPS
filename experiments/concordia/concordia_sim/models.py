"""The config->structure boundary: the params a single Concordia run accepts.

A pydantic model so a malformed config is rejected cleanly (before the heavy Concordia
import), with `extra="forbid"` so a typo'd key is an error, not a silently-ignored knob.

This is the sim program's FULL config surface — roster, premise, game master, and the
run knobs. The experiment cards in package.nix bake the scenario fields (agents,
premise, game_master) per experiment and expose only the comparison axes (models,
steps, generation knobs) as params: the scenario is the experiment, never a param.
The defaults here exist so a standalone/test invocation has a sensible scenario;
they never reach a real run, whose adapter always writes a complete config.
`seed` is not authored by a caller — the adapter merges `$REPS_SEED` in.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# the default roster/premise (the "two friends at a cafe" scene) for standalone/test
# runs; the concordia-cafe card bakes this same scenario via its adapter.
_DEFAULT_PREMISE = (
    "Alice and Bob, old friends who have not spoken in months, run into each other "
    "at a small cafe on a rainy afternoon."
)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the character's name (used as the message `from` in the transcript)
    name: str = Field(min_length=1)
    # what this character is trying to do — shapes how they act each turn
    goal: str = ""
    # per-agent model override as `provider/model`; "" = use the run's `model` param.
    # Lets a scenario mix models (e.g. one agent on qwen, the rest on the mock).
    model: str = ""


def _default_agents() -> list[AgentSpec]:
    return [
        AgentSpec(name="Alice", goal="Catch up warmly and find out how Bob has been."),
        AgentSpec(name="Bob", goal="Share what has changed in your life since you last met."),
    ]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the cast of the simulation — one row per character. Baked per experiment card.
    agents: list[AgentSpec] = Field(default_factory=_default_agents, min_length=2)

    # the opening situation the game master narrates to set the scene.
    premise: str = _DEFAULT_PREMISE

    # which Concordia game-master prefab drives the scene (a module name under
    # concordia.prefabs.game_master). `dialogic` is a pure conversation; `generic` narrates
    # events; any prefab name works (the runner streams the transcript regardless of GM).
    game_master: str = Field(default="dialogic", min_length=1)

    # how many simulation steps to run — one agent turn each, so with two agents this is
    # the number of messages in the conversation (6 -> three turns apiece).
    max_steps: int = Field(default=6, gt=0)

    # the default language model — drives the game master and every agent whose roster
    # row leaves `model` empty, as `provider/model`. `mock/...` runs keyless & offline;
    # `openai/<name>` is sent to an OpenAI-compatible /chat/completions server. The
    # endpoint + key are NOT params — they come from the credential set's env
    # (OPENAI_BASE_URL / OPENAI_API_KEY), which the runner injects from the local
    # credential store (`nix run .#reps-runner -- credentials set openai`). So the model
    # *name* is the condition; where it is served is not.
    default_model: str = Field(default="mock/model", min_length=1)

    # generation knobs for the real backend (ignored by the mock).
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, gt=0)

    # merged in from $REPS_SEED by the adapter; makes the mock backend deterministic.
    seed: int = 0
