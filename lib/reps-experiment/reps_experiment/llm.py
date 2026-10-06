"""An OpenAI-SDK-shaped chat client with REPS instrumentation built in.

For experiments that call models directly (their own loop, or a wrapped framework
that accepts an OpenAI client): construct `ChatClient(model_id, ...)` and hand it
wherever a `openai.OpenAI()` instance would go — it duck-types the one surface
frameworks actually use, ``.chat.completions.create``. In exchange:

  * the model id's provider prefix picks the endpoint and credential set
    (:mod:`reps_experiment.providers` — openai, anthropic, google, groq, mistral, grok,
    openrouter, azure, azureai; each an OpenAI-compatible mount), and ``mock/...``
    runs keyless and offline with a deterministic responder — the smoke/CI path,
    uniform across experiments (the runner's mock convention);
  * every call emits one ``llm.call`` event — the verbatim reply, token usage, and
    latency — attributed to the constructing `agent`;
  * run-level generation params apply uniformly: `temperature` overrides (it is the
    run's declared axis), `seed` fills in when the caller passes none, `max_tokens`
    caps whatever the caller asks for;
  * inline ``<think>`` blocks and provider reasoning become reasoning content
    parts; callers receive only text parts, with whitespace preserved.
    The raw SDK response is unchanged.
    Request-side reasoning settings are caller-owned.

A trailing assistant message is a prefill to continue. Mistral's serving API
only accepts one flagged ``prefix: True`` (message-level, not a generation
parameter), so the client adds the flag for the Mistral family before recording
and sending; other providers receive the message as given.

Needs the ``openai`` SDK — depend on ``reps-experiment[llm]``. Import stays inside this
module so the base package adds no requirement.
"""

from __future__ import annotations

from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import threading
import time
import types
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from pydantic import JsonValue

from reps_events import LLMCall, Log, chat_completion_output, content_filtered_without_model, emit
from reps_events.chat import assistant_message, chat_message
from reps_providers import model_family, served_model_matches
from reps_events.inspect_chat import (
    ChatCompletionChoice, ModelCall, ModelOutput, ToolChoice, ToolFunction, ToolInfo,
)

from .providers import resolve

if TYPE_CHECKING:
    import openai
    from openai.types.chat import ChatCompletion

# neutral deterministic lines for the default mock responder — enough variety that
# loops which detect repetition still make progress
_MOCK_LINES = (
    "That seems reasonable. Let's continue.",
    "Understood. Here is my considered response.",
    "Interesting — I had not thought of it that way.",
    "I agree with the direction so far.",
    "Let me suggest we take the next step.",
    "Fair enough. What would you like to do next?",
)

_MAX_RETRIES = 8


def deterministic_pick(seed: int, text: str, n: int) -> int:
    """A pure (seed, text) -> [0, n) index — the mock backend's only randomness."""
    digest = hashlib.sha256(f"{seed}|{text}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % n


def _tool_info(tool: dict[str, Any]) -> ToolInfo:
    function = tool.get("function") or tool.get("custom") or tool
    return ToolInfo(name=function["name"], description=function.get("description", ""),
                    parameters=function.get("parameters", {}),
                    options={k: v for k, v in function.items()
                             if k not in ("name", "description", "parameters")} or None)


def _tool_choice(value: Any) -> ToolChoice:
    if isinstance(value, str):
        if value == "required":
            return "any"
        if value in ("auto", "none"):
            return value
        raise ValueError(f"unsupported OpenAI tool choice: {value!r}")
    return ToolFunction(name=(value.get("function") or value["custom"])["name"])


class ServedModelMismatch(SystemExit):
    """Fatal routing error, deliberately outside harnesses' Exception fallbacks.

    Continuing with a default answer would conceal that this condition ran the
    wrong model. The original response and an error log are emitted before exit.
    """


class EmptyResponse(RuntimeError):
    """The provider returned no choices on every attempt."""


def _status_error_response(event: LLMCall, error: openai.APIStatusError) -> ChatCompletion | None:
    """Retain the full error body and construct choices as the SDK does for 200s."""
    from openai._models import construct_type_unchecked
    from openai.types.chat import ChatCompletion

    body: Any
    try:
        body = error.response.json()
    except ValueError:
        body = error.body if error.body is not None else error.response.text
    if event.call is not None:
        event.call.response = body if isinstance(body, dict) else {"body": body}
    if isinstance(body, dict):
        completion_body = cast(dict[str, Any], body)
        if isinstance(completion_body.get("choices"), list):
            return construct_type_unchecked(type_=ChatCompletion, value=completion_body)
    return None


def _record_response(event: LLMCall, response: ChatCompletion | None,
                     status_error: openai.APIStatusError | None) -> bool:
    """Snapshot output before caller-facing text changes; retain rejected bodies."""
    if response is None:
        return False
    body = response.model_dump(mode="json")
    if event.call is not None and status_error is None:
        event.call.response = body
    try:
        if body.get("choices") is not None:
            event.output = chat_completion_output(body)
    except (ValueError, TypeError, KeyError, AttributeError):
        if status_error is None:
            raise
        return False
    if not event.output.choices:
        return False
    if event.call is not None:
        event.call.response = body
    return True


class ChatClient:
    """See module docstring. `mock_responder` (messages -> str) customizes the mock
    backend's reply; the default picks deterministically from a neutral line bank."""

    def __init__(
        self,
        model_id: str,
        *,
        agent: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        temperature: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        mock_responder: Callable[[list[dict[str, Any]]], str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.agent = agent
        self.metadata: dict[str, JsonValue] = deepcopy(metadata or {})
        self.n_calls = 0
        self._count_lock = threading.Lock()
        self._model_lock = threading.Lock()
        self._model_checked = False
        self._retry_count: ContextVar[int] = ContextVar("call_retries", default=0)
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self.is_mock = model_id.startswith("mock/")
        self._mock_responder = mock_responder or self._default_mock_responder
        # one declared shape for both backends, so the lambda's param infers from it
        self._request: Callable[[dict[str, Any]], Any]
        if self.is_mock:
            self.served_model = model_id.split("/", 1)[1]
            self.base_url = ""
            self._request = self._mock_create  # unreached (_create short-circuits)
        else:
            import openai  # the [llm] extra; only this module needs it
            import httpx

            endpoint = resolve(model_id)  # ValueError with the fix in the message
            self.served_model = endpoint.served_model
            self.base_url = endpoint.base_url
            def count_retry(response: httpx.Response) -> None:
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    self._retry_count.set(self._retry_count.get() + 1)

            sdk = openai.OpenAI(
                api_key=endpoint.api_key, base_url=endpoint.base_url, max_retries=_MAX_RETRIES,
                # Preserve SDK timeout/connection defaults. Context-local counts
                # keep overlapping calls independent, including failed calls.
                http_client=openai.DefaultHttpxClient(event_hooks={"response": [count_retry]}),
            )

            # closed over, so _create never handles an Optional client; a def (not a
            # lambda) so the Any return is declared rather than inferred-unknown
            def request(kw: dict[str, Any]) -> Any:
                # The SDK overloads cannot resolve a dynamic **kw; declare Any
                # only where the SDK response enters the instrumentation.
                return cast(Any, sdk.chat.completions.create(**kw))

            self._request = request
        # the one surface frameworks use; duck-typed so no SDK subclassing is needed
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    # -- the instrumented create ---------------------------------------------

    def _create(self, **kw: Any) -> Any:
        messages = kw.get("messages")
        if (messages and messages[-1]["role"] == "assistant"
                and model_family(self.served_model) == "mistral"):
            kw["messages"] = [*messages[:-1], {**messages[-1], "prefix": True}]
        # the run's declared generation params, applied uniformly
        if self._temperature is not None:
            kw["temperature"] = self._temperature
        if kw.get("seed") is None and self._seed is not None:
            kw["seed"] = self._seed
        if self._max_tokens is not None:
            want = kw.get("max_completion_tokens") or kw.get("max_tokens")
            kw["max_completion_tokens"] = (
                self._max_tokens if want is None else min(want, self._max_tokens)
            )
            kw.pop("max_tokens", None)
        if self.is_mock:
            return self._mock_create(kw)
        event = self._event(kw)
        started = time.monotonic()
        import openai

        response: Any = None
        token = self._retry_count.set(0)
        try:
            for attempt in range(1, _MAX_RETRIES + 1):
                event.output = ModelOutput()
                if event.call is not None:
                    event.call.response = None
                error: openai.APIStatusError | None = None
                try:
                    response = self._request(kw)
                except openai.APIStatusError as exc:
                    error = exc
                    response = _status_error_response(event, exc)
                if _record_response(event, response, error):
                    break
                if error is not None:
                    raise error
                # Like the HTTP retry hook, count the final empty attempt too.
                self._retry_count.set(self._retry_count.get() + 1)
                if attempt == _MAX_RETRIES:
                    raise EmptyResponse(
                        f"Empty choices for model {self.model_id!r} after {attempt} attempts "
                        f"(last response id: {getattr(response, 'id', None)!r})"
                    )
                time.sleep(min(2 ** (attempt - 1), 60))
        except Exception as exc:
            event.error = str(exc)
            if event.call is not None and event.call.response is None:
                event.call.error = True
            raise
        finally:
            event.retries = self._retry_count.get()
            self._retry_count.reset(token)
            event.working_time = time.monotonic() - started
            if event.error is not None:
                self._emit(event)
        assert response is not None
        # Check the first successful response for each client/model in the run.
        # The verifier checks every recorded call, including later alias drift.
        # Serialize capture with the check so overlapping responses cannot race
        # to mark the model checked between another call's emission and check.
        with self._model_lock:
            self._emit(event)
            if not self._model_checked and not content_filtered_without_model(event.output):
                if not served_model_matches(self.model_id, event.output.model):
                    message = f"Served model mismatch: requested {self.model_id!r}, served {event.output.model!r}"
                    emit(Log(level="error", message=message))
                    raise ServedModelMismatch(message)
                self._model_checked = True
        if response.choices:
            response.choices[0].message.content = event.output.completion
        return response

    # -- the mock backend -----------------------------------------------------

    def _default_mock_responder(self, messages: list[dict[str, Any]]) -> str:
        prompt = str((messages[-1] or {}).get("content", "")) if messages else ""
        return _MOCK_LINES[deterministic_pick(self._seed or 0, prompt,
                                              len(_MOCK_LINES))]

    def _mock_create(self, kw: dict[str, Any]) -> Any:
        text = self._mock_responder(kw.get("messages") or [])
        event = self._event(kw)
        event.retries = 0
        message = assistant_message({"content": text})
        event.output = ModelOutput(
            model=self.served_model, completion=message.text,
            choices=[ChatCompletionChoice(message=message, stop_reason="stop")],
        )
        self._emit(event)
        # the OpenAI response shape consumers read: choices[0].message.content
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(role="assistant", content=event.output.completion),
                finish_reason="stop",
            )],
            usage=None,
            model=self.served_model,
        )

    # -- event emission --------------------------------------------------------

    def _event(self, kw: dict[str, Any]) -> LLMCall:
        snapshot = deepcopy(kw)
        return LLMCall(
            model=self.model_id, agent=self.agent,
            input=[chat_message(m) for m in snapshot.get("messages", [])],
            tools=[_tool_info(t) for t in snapshot.get("tools", [])],
            tool_choice=_tool_choice(snapshot.get("tool_choice", "auto")),
            # A failed request has no served model; do not substitute the request
            # name into evidence or the card's observed served-model set.
            output=ModelOutput(),
            call=ModelCall(request=snapshot),
            metadata=deepcopy(self.metadata) or None,
        )

    def _emit(self, event: LLMCall) -> None:
        with self._count_lock:
            self.n_calls += 1
        event.completed = datetime.now(timezone.utc)
        emit(event)
