"""The LangChain chat model that stands in for upstream's model construction.

Upstream builds its model once per run in `LiteralMessagePassing.__init__`:
`init_chat_model(model_name, model_provider=..., rate_limiter=..., **chat_kwargs)
.with_retry(stop_after_attempt=10)`, or `ChatOllama(model, base_url)` for the
ollama provider. Every node then calls that one object through a langgraph
`StateGraph` with a `MemorySaver`, one `thread_id` per node, so the full
conversation of each agent is replayed on every call.

`RepsChatModel` is a `BaseChatModel` whose completions go through REPS's
`ChatClient`, so every call is one `llm.call` event with the full message list
and the verbatim reply, the provider is chosen by the run's model id, and
`mock/` runs keyless. The node a call belongs to arrives as the langgraph
`thread_id` in the callback metadata; the model keeps one `ChatClient` per node
so the event's `agent` is that node's name.

What is sent, compared with the LangChain classes upstream would have built at
the pinned versions: the same message dicts (langchain-openai's own converters);
no `temperature` for OpenAI and Anthropic (their classes send none),
`temperature` 0.7 for Google (langchain-google-genai's class default); and
`max_tokens` 1024 for Anthropic (langchain-anthropic's class default) unless
upstream passes its own. The run's seed on every request (the REPS client adds
it; upstream sent none). Upstream's thinking variants pass `thinking`,
`thinking_budget` and `include_thoughts` kwargs that have no OpenAI-protocol
form; they are recorded as request metadata and not forwarded.

A real reply is turned into LangChain messages by langchain-openai's own
`_create_chat_result`, so the AIMessage carries the same `usage_metadata` and
`response_metadata` (token usage, model name, finish reason, response id) that
ChatOpenAI would have given it, and upstream's transcripts record them as they
would have. Two things differ from ChatOpenAI and are REPS-wide: the client
lifts an inline <think> block out of the text into a reasoning part before
upstream parses the reply, and a content-filter 400 whose body carries
`choices` is returned as an empty reply (ChatOpenAI raises; upstream's
with_retry would then re-raise after ten identical attempts and the run would
die). Retries compound: with_retry's ten attempts wrap the REPS client's own
eight.
"""

from __future__ import annotations

import threading
import types
from typing import Any, Callable, Optional

from pydantic import ConfigDict, PrivateAttr
from reps_events import Log, emit
from reps_experiment.llm import ChatClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai.chat_models.base import (BaseChatOpenAI, _convert_dict_to_message,
                                               _convert_message_to_dict)

# what the LangChain class upstream would have constructed sends by default
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "google": {"temperature": 0.7},        # ChatGoogleGenerativeAI.temperature = 0.7
    "anthropic": {"max_tokens": 1024},     # ChatAnthropic.max_tokens = 1024
}
# upstream kwargs for its "-thinking" model names that the OpenAI chat protocol
# has no field for; recorded, not sent
UNFORWARDED_KWARGS = ("thinking", "thinking_budget", "include_thoughts")

# every model the run constructs, so model_calls can be summed at the end
MODELS: list["RepsChatModel"] = []


def model_calls() -> int:
    return sum(client.n_calls for model in MODELS for client in model.clients().values())


def reset_models() -> None:
    MODELS.clear()


class RepsChatModel(BaseChatModel):
    """See the module docstring. `agent_names` maps langgraph thread ids (upstream
    uses `str(node_id)`) to node names for `llm.call` attribution."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_id: str                              # provider/model (the run's)
    run_seed: int
    agent_names: dict[str, str]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    upstream_kwargs: dict[str, Any] = {}       # what upstream passed to init_chat_model / ChatOllama
    mock_responder: Any = None                 # (messages) -> str, for mock/ ids
    _clients: dict[Optional[str], ChatClient] = PrivateAttr(default_factory=dict)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "reps-chat-client"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model_id, "temperature": self.temperature, "max_tokens": self.max_tokens,
                "upstream_kwargs": self.upstream_kwargs}

    def clients(self) -> dict[Optional[str], ChatClient]:
        return dict(self._clients)

    def client_for(self, agent: Optional[str]) -> ChatClient:
        with self._lock:
            client = self._clients.get(agent)
            if client is None:
                client = ChatClient(self.model_id, agent=agent, seed=self.run_seed,
                                    mock_responder=self.mock_responder,
                                    metadata={"agentsnet.upstream_kwargs": self.upstream_kwargs} or None)
                self._clients[agent] = client
            return client

    def _agent(self, run_manager: Optional[CallbackManagerForLLMRun]) -> Optional[str]:
        metadata = getattr(run_manager, "metadata", None) or {}
        thread_id = metadata.get("thread_id")
        return self.agent_names.get(str(thread_id)) if thread_id is not None else None

    def _request(self, client: ChatClient, messages: list[BaseMessage], stop: Optional[list[str]]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": client.served_model,
            "messages": [_convert_message_to_dict(m) for m in messages],
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        if self.max_tokens is not None:
            request["max_tokens"] = self.max_tokens
        if stop:
            request["stop"] = stop
        return request

    def _generate(self, messages: list[BaseMessage], stop: Optional[list[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None,
                  **kwargs: Any) -> ChatResult:
        client = self.client_for(self._agent(run_manager))
        response = client.chat.completions.create(**self._request(client, messages, stop))
        if client.is_mock:   # a bare text reply (the mock backend has no usage or ids)
            message = _convert_dict_to_message({"role": "assistant", "content": response.choices[0].message.content or ""})
            if not isinstance(message, AIMessage):
                message = AIMessage(content=str(message.content))
            return ChatResult(generations=[ChatGeneration(message=message)])
        # the SDK response, through langchain-openai's own converter: message, usage_metadata,
        # response_metadata and llm_output exactly as ChatOpenAI builds them (its only use of
        # `self` is the model name fallback)
        return BaseChatOpenAI._create_chat_result(
            types.SimpleNamespace(model_name=client.served_model), response)  # type: ignore[arg-type]


def make_chat_model(model_id: str, *, run_seed: int, agent_names: dict[str, str],
                    upstream_kwargs: Optional[dict[str, Any]] = None,
                    mock_responder: Optional[Callable[[list[dict[str, Any]]], str]] = None) -> RepsChatModel:
    """The REPS-backed model for upstream's one construction site. `upstream_kwargs`
    are the keyword arguments upstream passed (its thinking variants add
    max_tokens and thinking settings); `max_tokens` is forwarded, the rest recorded."""
    provider = model_id.split("/", 1)[0]
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    kwargs = dict(upstream_kwargs or {})
    dropped = {k: kwargs[k] for k in UNFORWARDED_KWARGS if k in kwargs}
    if dropped:
        emit(Log(level="warn", message=f"agentsnet: upstream passed {sorted(dropped)} for this model name; "
                                       "the OpenAI chat protocol has no field for them and they are not sent"))
    model = RepsChatModel(
        model_id=model_id, run_seed=run_seed, agent_names=agent_names,
        temperature=kwargs.get("temperature", defaults.get("temperature")),
        max_tokens=kwargs.get("max_tokens", defaults.get("max_tokens")),
        upstream_kwargs=kwargs, mock_responder=mock_responder,
    )
    MODELS.append(model)
    return model
