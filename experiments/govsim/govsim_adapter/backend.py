"""ChatClientBackend — REPS's instrumented ChatClient as a pathfinder backend.

pathfinder's ModelAPI already emulates gen/find/select over one buffered chat
completion; ``request_api`` is its single abstract hook. Subclassing it (library
use, never a fork) routes every GovSim model call through REPS's ChatClient:
``mock/...`` runs keyless and offline, any provider prefix reaches its
OpenAI-compatible mount, and each completion emits one ``llm.call`` event.

Everything else — the roles state machine, ``text_to_consume`` caching (one
completion shared by a chain's gen→find→select), stop-regex parsing, prefix
stripping — is inherited unchanged. Parse failures raise here; upstream's
``ModelWandbWrapper`` catches them and substitutes ``default_value`` (upstream
robustness semantics, kept verbatim).
"""

from __future__ import annotations

from collections.abc import Callable

from reps_experiment.llm import ChatClient
from pathfinder.api import ModelAPI


def _strip_prefill_ws(messages: list[dict]) -> list[dict]:
    """Anthropic rejects a final assistant (prefill) message ending in whitespace;
    upstream prompts prefill e.g. "Answer: "."""
    if messages and messages[-1]["role"] == "assistant":
        c = messages[-1]["content"] or ""
        if c != c.rstrip():
            messages[-1] = {**messages[-1], "content": c.rstrip()}
    return messages


class ChatClientBackend(ModelAPI):
    """One shared backend serves every persona plus the framework (upstream's
    single-LLM shape). ``client.agent`` is read at emit time, so the RepsLogger
    hook re-points it per persona for llm.call attribution."""

    def __init__(
        self,
        model_id: str,
        seed: int,
        *,
        temperature: float | None = None,
        top_p: float | None = 1.0,
        reasoning_effort: str | None = None,
        max_tokens: int | None = None,
        mock_responder: Callable[[list[dict]], str] | None = None,
    ) -> None:
        # api_assistant=True: upstream's assistant prefills are sent as a trailing
        # assistant message; ChatClient adds Mistral's prefix flag where required.
        super().__init__(model_id, seed, api_assistant=True)
        # The upstream wrapper replaces top_p=None with 1.0 (and select uses
        # fixed sampling values). Honor explicit run-level omission here.
        self.omit_temperature = temperature is None
        self.omit_top_p = top_p is None
        self.reasoning_effort = reasoning_effort
        self.client = ChatClient(
            model_id,
            agent="framework",
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
            mock_responder=mock_responder,
        )

    # upstream hook (ModelAPI.run / run_find call this; the typo'd parameter name
    # is upstream's positional signature)
    def request_api(self, chat, tmeperature, top_p, max_tokens):
        generation = {}
        if not self.omit_temperature:
            generation["temperature"] = tmeperature
        if not self.omit_top_p:
            generation["top_p"] = top_p
        if self.reasoning_effort is not None:
            generation["reasoning_effort"] = self.reasoning_effort
        out = self.client.chat.completions.create(
            # real backends must see the served name (concordia rule); the mock
            # ignores it
            model=self.client.served_model,
            messages=_strip_prefill_ws(list(chat)),
            **generation,
            max_tokens=max_tokens,
            seed=self.seed,
        )
        # Pathfinder anchors prefix removal and stop-pattern matches at position 0;
        # API providers add leading whitespace that local generation never does.
        return (out.choices[0].message.content or "").strip()
