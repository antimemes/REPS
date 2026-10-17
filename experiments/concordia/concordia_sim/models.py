"""The config->structure boundary: the params a single Concordia run accepts.

A pydantic model to reject a malformed config cleanly (before the heavy Concordia
import), with `extra="forbid"` to make an incorrect key an error, not a knob that
is ignored with no warning.

This is the sim program's FULL config surface — roster, premise, game master, and the
run knobs. The experiment cards in package.nix bake the scenario fields (agents,
premise, game_master) for each experiment and expose only the comparison axes (models,
steps, generation knobs) as params: the scenario is the experiment, not a param.
The defaults here give a standalone/test invocation a sensible scenario;
they do not reach a real run, whose adapter always writes a complete config.
`seed` is not authored by a caller — the adapter merges `$REPS_SEED` in.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# the default roster/premise (the "two friends at a cafe" scene) for standalone/test
# runs; the concordia-cafe card bakes this same scenario through its adapter.
_DEFAULT_PREMISE = (
    "Alice and Bob, old friends who have not spoken in months, run into each other "
    "at a small cafe on a rainy afternoon."
)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the character's name (used as the message `from` in the transcript)
    name: str = Field(min_length=1)
    # the thing this character is trying to do — shapes how they act each turn
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

    # the cast of the simulation — one row for each character. Baked for each experiment card.
    agents: list[AgentSpec] = Field(default_factory=_default_agents, min_length=2)

    # the opening situation the game master narrates to set the scene.
    premise: str = _DEFAULT_PREMISE

    # which Concordia game-master prefab drives the scene (a module name in
    # concordia.prefabs.game_master). `dialogic` is a pure conversation; `generic` narrates
    # events; each prefab name works (the runner streams the transcript regardless of GM).
    game_master: str = Field(default="dialogic", min_length=1)

    # how many simulation steps to run — one agent turn each. Thus with two agents this is
    # the number of messages in the conversation (6 -> three turns apiece).
    max_steps: int = Field(default=6, gt=0)

    # the default language model — drives the game master and each agent whose roster
    # row leaves `model` empty, as `provider/model`. `mock/...` runs keyless & offline;
    # `openai/<name>` is sent to an OpenAI-compatible /chat/completions server. The
    # endpoint + key are NOT params — they come from the credential set's env
    # (OPENAI_BASE_URL / OPENAI_API_KEY), which the runner injects from the local
    # credential store (`nix run .#reps-runner -- credentials set openai`). Thus the model
    # *name* is the condition; where it is served is not.
    default_model: str = Field(default="mock/model", min_length=1)

    # generation knobs for the real backend (ignored by the mock).
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, gt=0)

    # merged in from $REPS_SEED by the adapter; makes the mock backend deterministic.
    seed: int = 0
