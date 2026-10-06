"""Exercise the actual SDK transport, retries, and model-identity boundary."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
import json

import httpx
import openai
import pytest

from adb_experiment.llm import ChatClient, EmptyResponse, ServedModelMismatch
from adb_experiment.providers import resolve
from test_llm import client_and_reply


def test_azure_resolution_requires_both_credentials():
    env = {"AZURE_OPENAI_API_KEY": "test-key", "AZURE_OPENAI_BASE_URL": "https://resource.openai.azure.com/openai/v1/"}
    endpoint = resolve("azure/gpt-5-nano", env)
    assert endpoint.served_model == "gpt-5-nano"
    assert endpoint.base_url == env["AZURE_OPENAI_BASE_URL"].rstrip("/")
    assert endpoint.api_key == "test-key"
    for key in env:
        with pytest.raises(ValueError, match=key):
            resolve("azure/gpt-5-nano", {k: v for k, v in env.items() if k != key})


@pytest.mark.parametrize("requested,served", [
    ("alias", "wrong-model"),
    ("gpt-4o-2024-11-20", "gpt-4o-2024-08-06"),
    ("gpt-4o-2024-11-20", "gpt-4o"),
])
def test_first_success_records_mismatch_then_fails_without_harness_fallback(event_capture, requested, served):
    client, reply = client_and_reply()
    client.model_id = f"mock/{requested}"
    reply.model = served
    client._request = lambda kw: reply
    original = reply.model_dump(mode="json")
    with pytest.raises(ServedModelMismatch, match=f"mock/{requested}.*{served}"):
        # A harness may recover ordinary API failures, but not a misrouted model.
        try:
            client.chat.completions.create(model=requested, messages=[])
        except Exception:
            pytest.fail("model mismatch was swallowed by a harness fallback")
    call, log = event_capture.read()
    assert call["type"] == "llm.call" and call["output"]["model"] == served
    assert call["call"]["response"] == original
    assert call.get("error") is None
    assert log["type"] == "log" and log["level"] == "error"
    assert f"mock/{requested}" in log["message"] and served in log["message"]


def sdk_client(monkeypatch, handler):
    default_http = openai.DefaultHttpxClient

    def transport(**kw):
        return default_http(**kw, transport=httpx.MockTransport(handler))

    sdk_type = openai.OpenAI

    def sdk(**kw):
        assert kw["max_retries"] == 8
        assert kw["http_client"].timeout == openai.DEFAULT_TIMEOUT
        return sdk_type(**kw)

    monkeypatch.setattr(openai, "DefaultHttpxClient", transport)
    monkeypatch.setattr(openai, "OpenAI", sdk)
    monkeypatch.setattr(openai._base_client.BaseClient, "_calculate_retry_timeout", lambda *a, **kw: 0)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://resource.openai.azure.com/openai/v1/")
    return ChatClient("azure/alias")


def test_http_and_empty_retries_are_per_call_including_overlapping_calls(event_capture, monkeypatch):
    _, reply = client_and_reply()
    attempts = {}
    lock, barrier = Lock(), Barrier(2)

    def handler(request):
        assert request.url.path == "/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "alias"
        name = body["messages"][0]["content"]
        with lock:
            attempts[name] = attempts.get(name, 0) + 1
            attempt = attempts[name]
        if attempt == 1 and name in {"retry", "clean"}:
            barrier.wait(timeout=5)
        code = [429, 503, 200][min(attempt - 1, 2)] if name == "retry" else 200
        body = reply.model_dump(mode="json") if code == 200 else {"error": {"message": "busy"}}
        if name == "retry" and attempt == 3:
            body.update(model="", choices=[])
        return httpx.Response(code, json=body)

    client = sdk_client(monkeypatch, handler)
    monkeypatch.setattr("adb_experiment.llm.time.sleep", lambda delay: None)
    def call(name):
        return client.chat.completions.create(model="alias", messages=[{"role": "user", "content": name}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(call, ["retry", "clean"]))
    call("next")
    events = {e["call"]["request"]["messages"][0]["content"]: e for e in event_capture.read()}
    assert attempts == {"retry": 4, "clean": 1, "next": 1}
    assert events["retry"]["retries"] == 3
    assert all(events[name]["retries"] == 0 for name in ("clean", "next"))
    assert all(event["metadata"] is None for event in events.values())


def test_exhausted_retries_retained_and_do_not_consume_first_success_check(event_capture, monkeypatch):
    count = 0
    _, reply = client_and_reply()
    reply.model = "wrong-model"

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(429, json={"error": {"message": "busy"}}) if count <= 9 else httpx.Response(200, json=reply.model_dump(mode="json"))

    client = sdk_client(monkeypatch, handler)
    with pytest.raises(openai.RateLimitError):
        client.chat.completions.create(model="alias", messages=[])
    with pytest.raises(ServedModelMismatch):
        client.chat.completions.create(model="alias", messages=[])
    failure, success, log = event_capture.read()
    assert failure["retries"] == 9
    assert failure["metadata"] is None
    assert failure["error"] and failure["call"]["error"] is None
    assert failure["output"]["model"] == ""
    assert success["retries"] == 0
    assert success["metadata"] is None
    assert log["level"] == "error"


@pytest.mark.parametrize("status,body", [
    (400, {"error": {"message": "no output", "type": "bad_request"}, "request_id": "retained"}),
    (400, {"id": "empty", "model": "alias", "choices": []}),
    (400, {"id": "empty", "object": "chat.completion", "created": 123, "model": "alias", "choices": [],
           "usage": {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3}}),
    (200, {"id": "empty", "model": "alias", "choices": []}),
    (200, {"id": "missing", "model": "alias"}),
])
def test_transport_without_choices_retries_only_success_status_and_retains_body(event_capture, monkeypatch, status, body):
    attempts = []
    delays = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, json=body)

    client = sdk_client(monkeypatch, handler)
    monkeypatch.setattr("adb_experiment.llm.time.sleep", delays.append)
    with pytest.raises(openai.BadRequestError if status == 400 else EmptyResponse) as caught:
        client.chat.completions.create(model="alias", messages=[])
    assert len(attempts) == (1 if status == 400 else 8)
    assert delays == ([] if status == 400 else [1, 2, 4, 8, 16, 32, 60])
    [event] = event_capture.read()
    assert event["error"] == str(caught.value)
    assert event["call"]["error"] is None
    assert not event["output"]["choices"]
    assert event["retries"] == (0 if status == 400 else 8)
    assert all(event["call"]["response"][key] == value for key, value in body.items())
    if status == 400:
        assert event["call"]["response"] == body
    assert not client._model_checked
    if "usage" in body:
        assert event["output"]["model"] == body["model"]
        assert event["output"]["usage"]["input_tokens"] == 3


@pytest.mark.parametrize("final_status", [200, 400])
def test_empty_success_responses_retry_into_content_filter_observation(event_capture, monkeypatch, final_status):
    _, reply = client_and_reply()
    reply.choices[0].finish_reason = "content_filter"
    reply.choices[0].message.content = ""
    body = reply.model_dump(mode="json")
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(200, json={"model": "alias", "choices": []})
        return httpx.Response(final_status, json=body)

    client = sdk_client(monkeypatch, handler)
    monkeypatch.setattr("adb_experiment.llm.time.sleep", lambda delay: None)
    result = client.chat.completions.create(model="alias", messages=[])
    assert result.choices[0].finish_reason == "content_filter"
    assert len(attempts) == 3
    [event] = event_capture.read()
    assert event["error"] is None
    assert event["call"]["error"] is None
    assert event["call"]["response"] == body
    assert event["output"]["choices"][0]["stop_reason"] == "content_filter"
    assert event["retries"] == 2
