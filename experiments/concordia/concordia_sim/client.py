"""The language model behind every agent and the game master.

One class implementing Concordia's `LanguageModel` interface (`sample_text` /
`sample_choice`) by delegating to Concordia's own ``BaseGPTModel``
(concordia.contrib), which owns all prompt-shaping (the continuation system message
+ few-shot examples) and the multiple-choice retry loop. Underneath it sits
:class:`reps_experiment.llm.ChatClient` — the REPS-instrumented OpenAI-compat client:
provider routing from the model id's prefix, one ``llm.call`` event per call with
usage and latency, reasoning-tag stripping, and the keyless ``mock/`` backend.

The mock rides the same BaseGPTModel path as real models — only the responder is
swapped (deterministic scripted lines; exact-option answers for choice prompts; a
valid action spec when the game master asks for one), so the smoke/CI run
exercises upstream's prompt assembly too.

Every call — mock or real — emits one ``llm.call`` event, so the event stream has
the same shape keyless as it does against a real model.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from concordia.contrib.language_models.openai.base_gpt_model import BaseGPTModel
from concordia.language_model import language_model
from concordia.prefabs.simulation import generic as generic_simulation

from reps_events import Log, emit
from reps_experiment.llm import ChatClient, deterministic_pick

# a small bank of neutral, conversation-shaped replies for the mock backend; picking
# by hash keeps the mock deterministic while varying enough that Concordia's
# repetition detector still makes progress.
_MOCK_LINES = (
    "That's good to hear. Tell me more about it.",
    "I understand. Things have been much the same on my end.",
    "Interesting — I hadn't thought of it that way.",
    "Well, I suppose we can find some agreement there.",
    "It has been far too long. Let's not leave it so long next time.",
    "Fair enough. What did you have in mind?",
)

# The game master asks the model to emit the *next action spec* as JSON (see
# Concordia's next_acting.py). A free-text reply fails its parser, so the mock
# returns a valid, always open-ended spec when it recognises that prompt — keeping
# the smoke run on rails.
_MOCK_ACTION_SPEC = (
    '{"call_to_action": "What happens next?", "output_type": "free", '
    '"options": [], "tag": null}'
)

# BaseGPTModel.sample_choice's phrasing — the mock answers those prompts with one
# of the listed options verbatim, so upstream's exact-match loop accepts it.
_CHOICE_MARKER = "Respond EXACTLY with one of the following strings:"


def _wants_action_spec(prompt: str) -> bool:
    low = prompt.lower()
    return "action spec" in low or "output_type" in low


class RepsLanguageModel(language_model.LanguageModel):
    """A Concordia LanguageModel that emits REPS llm.call events on every call.

    One instance per calling entity: `model` overrides the run's default model id
    (the per-agent `model` field in the roster), and `agent` names the entity in
    every llm.call event — Concordia itself gives the model no calling-entity
    context, so attribution has to ride in on the instance.
    """

    def __init__(self, params, *, model: str | None = None, agent: str | None = None) -> None:
        # params: models.Params (avoid the import cycle)
        model_id = model or params.default_model
        self._seed = params.seed
        # A model this client can't serve, or a missing/malformed endpoint, cannot
        # produce a single useful call — fail the run now, with the fix in the
        # message, instead of limping through max_steps of request_failed noise.
        try:
            self._chat = ChatClient(
                model_id,
                agent=agent,
                temperature=params.temperature,
                seed=params.seed,
                max_tokens=params.max_tokens,
                mock_responder=self._mock_respond,
            )
        except ValueError as exc:
            emit(Log(message=str(exc), level="error"))
            raise RuntimeError(str(exc)) from None
        self._served = self._chat.served_model
        self._base_url = self._chat.base_url
        self._inner = BaseGPTModel(model_name=self._served, client=self._chat)

    @property
    def n_calls(self) -> int:
        return self._chat.n_calls

    # -- Concordia interface -------------------------------------------------

    def sample_text(
        self,
        prompt: str,
        *,
        max_tokens: int = language_model.DEFAULT_MAX_TOKENS,
        terminators: Collection[str] = language_model.DEFAULT_TERMINATORS,
        temperature: float = language_model.DEFAULT_TEMPERATURE,
        top_p: float = language_model.DEFAULT_TOP_P,
        top_k: int = language_model.DEFAULT_TOP_K,
        timeout: float = language_model.DEFAULT_TIMEOUT_SECONDS,
        seed: int | None = None,
    ) -> str:
        return self._inner.sample_text(
            prompt,
            max_tokens=max_tokens,
            terminators=terminators,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            timeout=timeout,
            seed=seed,
        )

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
    ) -> tuple[int, str, Mapping[str, Any]]:
        if not responses:
            raise language_model.InvalidResponseError("no responses to choose from")
        return self._inner.sample_choice(prompt, responses, seed=seed)

    # -- the mock responder ----------------------------------------------------

    def _mock_respond(self, messages: list[dict]) -> str:
        prompt = str((messages[-1] or {}).get("content", "")) if messages else ""
        if _CHOICE_MARKER in prompt:
            tail = prompt.rsplit(_CHOICE_MARKER, 1)[1].strip().removesuffix(".")
            options = [line.strip() for line in tail.splitlines() if line.strip()]
            if options:
                return options[deterministic_pick(self._seed, prompt, len(options))]
        if _wants_action_spec(prompt):
            return _MOCK_ACTION_SPEC
        return _MOCK_LINES[deterministic_pick(self._seed, prompt, len(_MOCK_LINES))]


class PerEntitySimulation(generic_simulation.Simulation):
    """Upstream's Simulation is multi-model at three grains — default,
    `override_agent_model` (all entities), `override_game_master_model` — but
    `add_entity` builds every entity with the one shared `_agent_model`. We need
    per-entity grain for two REPS reasons upstream doesn't have: llm.call attribution
    requires one client per entity (Concordia gives the model no calling-entity
    context), and the roster's per-agent model override is a treatment axis riding
    the same seam. add_entity is the narrowest seam: swap the model in for the one
    entity being built, restore it after, delegate everything else.

    `clients` maps entity name -> RepsLanguageModel; names not in the map build with
    the shared model as upstream would.
    """

    def __init__(self, *, config, model, embedder,
                 clients: dict[str, RepsLanguageModel]) -> None:
        self._clients = dict(clients)  # before super().__init__ — it builds entities
        super().__init__(config=config, model=model, embedder=embedder)

    def add_entity(self, instance_config, state=None):
        client = self._clients.get((instance_config.params or {}).get("name", ""))
        if client is None:
            return super().add_entity(instance_config, state)
        saved = self._agent_model
        self._agent_model = client
        try:
            return super().add_entity(instance_config, state)
        finally:
            self._agent_model = saved
