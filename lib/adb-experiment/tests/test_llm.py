"""Capture must describe the effective SDK call, including provider evidence."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from openai.types.chat import ChatCompletion

from adb_events.inspect_chat import ChatMessageAssistant, ContentReasoning, ContentText
from adb_experiment.llm import ChatClient, EmptyResponse, ServedModelMismatch
from adb_providers import PROVIDERS


def client_and_reply():
    client = ChatClient("mock/alias", temperature=0.2, seed=17, max_tokens=80)
    client.is_mock = False
    reply = ChatCompletion.model_validate(
        {
            "id": "call-123",
            "object": "chat.completion",
            "created": 123,
            "model": "alias-2026-09-16",
            "system_fingerprint": "fp_123",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "<think>private</think>answer",
                        "vendor_field": {"future": True},
                    },
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            "vendor_field": {"future": [None, 1]},
        }
    )
    return client, reply


def test_capture_matches_effective_sdk_request_and_original_response(event_capture):
    client, reply = client_and_reply()
    sent = {}
    original = reply.model_dump(mode="json")

    def request(kw):
        sent.update(deepcopy(kw))
        return reply

    client._request = request
    result = client.chat.completions.create(
        model="sent-model",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.9,
        top_p=0.8,
        max_tokens=100,
    )
    event = event_capture.read()[0]
    assert event["call"]["request"] == sent
    assert not {"params", "role", "cache", "instance_id", "repeat"} & event.keys()
    assert event["retries"] == 0
    assert event["input"][0]["content"] == "hello"
    assert event["call"]["request"]["temperature"] == 0.2
    assert event["call"]["request"]["max_completion_tokens"] == 80
    assert event["call"]["request"]["seed"] == 17
    assert event["call"]["response"] == original
    assert event["output"]["model"] == "alias-2026-09-16"
    assert event["call"]["response"]["system_fingerprint"] == "fp_123"
    assert ChatMessageAssistant.model_validate(event["output"]["choices"][0]["message"]).content == [
        ContentReasoning(reasoning="private"), ContentText(text="answer"),
    ]
    assert event["output"]["completion"] == "answer"
    assert result.choices[0].message.content == "answer"
    assert event["metadata"] is None
    assert event["output"]["usage"]["input_tokens"] == 3
    assert event["output"]["usage"]["output_tokens"] == 5



def test_failure_retains_effective_request(event_capture):
    client, _ = client_and_reply()
    client.metadata = {"test.phase": "harvest"}

    def fail(kw):
        raise RuntimeError("connection failed")

    client._request = fail
    with pytest.raises(RuntimeError, match="connection failed"):
        client.chat.completions.create(model="sent", messages=[], top_p=0.8)
    event = event_capture.read()[0]
    assert event["call"]["request"]["temperature"] == 0.2
    assert event["call"]["request"]["top_p"] == 0.8
    assert event["error"] == "connection failed"
    assert event["call"]["error"] is True
    assert event["output"]["choices"] == []
    assert event["call"]["response"] is None
    assert event["metadata"]["test.phase"] == "harvest"


@pytest.fixture
def sdk_request_client(monkeypatch):
    request, sleep = Mock(), Mock()
    monkeypatch.setattr("openai.resources.chat.completions.Completions.create", request)
    monkeypatch.setattr("adb_experiment.llm.time.sleep", sleep)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://resource.openai.azure.com/openai/v1/")
    client = ChatClient("azure/alias", temperature=0.2, seed=17, max_tokens=80)
    return client, request, sleep


def test_empty_responses_retry_until_a_choice_is_returned(event_capture, sdk_request_client):
    client, request, sleep = sdk_request_client
    _, reply = client_and_reply()
    empty = reply.model_copy(update={"model": "", "choices": []})
    request.side_effect = [empty, empty, reply]
    original = reply.model_dump(mode="json")

    result = client.chat.completions.create(model="sent", messages=[])

    assert result is reply
    assert result.choices[0].message.content == "answer"
    assert request.call_count == 3
    assert sleep.call_args_list == [call(1), call(2)]
    assert client.n_calls == 1
    [event] = event_capture.read()
    assert event["retries"] == 2
    assert event["error"] is None
    assert not event["call"]["error"]
    assert event["call"]["response"] == original
    assert all(attempt.kwargs == event["call"]["request"] for attempt in request.call_args_list)


def test_exhausted_empty_responses_record_failure_and_last_id(event_capture, sdk_request_client):
    client, request, sleep = sdk_request_client
    _, reply = client_and_reply()
    replies = [reply.model_copy(update={"id": f"empty-{i}", "model": "", "choices": []})
               for i in range(8)]
    request.side_effect = replies

    with pytest.raises(EmptyResponse, match="azure/alias.*8 attempts.*empty-7") as failure:
        client.chat.completions.create(model="sent", messages=[])

    assert request.call_count == 8
    assert sleep.call_args_list == [call(delay) for delay in (1, 2, 4, 8, 16, 32, 60)]
    assert client.n_calls == 1
    [event] = event_capture.read()
    assert event["retries"] == 8
    assert event["error"] == str(failure.value)
    assert event["call"]["error"] is True
    assert event["call"]["response"] is None
    assert event["call"]["request"]["seed"] == 17
    assert event["output"]["choices"] == []
    assert event["output"]["model"] == ""
    assert event["output"]["usage"] is None
    assert event["working_time"] >= 0
    assert event["completed"] is not None

    # Exhaustion must not consume the first-success routing check or leak retries.
    reply.model = "wrong-model"
    request.side_effect = None
    request.return_value = reply
    with pytest.raises(ServedModelMismatch):
        client.chat.completions.create(model="sent", messages=[])
    success, log = event_capture.read()
    assert success["retries"] == 0
    assert log["level"] == "error"


@pytest.mark.parametrize("content", ["", None])
def test_empty_content_with_a_choice_is_not_retried(event_capture, sdk_request_client, content):
    client, request, sleep = sdk_request_client
    _, reply = client_and_reply()
    reply.choices[0].message.content = content
    request.return_value = reply

    result = client.chat.completions.create(model="sent", messages=[])

    assert result.choices[0].message.content == ""
    request.assert_called_once()
    sleep.assert_not_called()
    [event] = event_capture.read()
    assert event["retries"] == 0
    assert event["error"] is None
    assert len(event["output"]["choices"]) == 1


def test_producer_metadata_is_snapshotted_and_stays_out_of_request(event_capture):
    client, reply = client_and_reply()
    client.metadata = {"test.context": {"phase": "first"}}

    def request(kw):
        assert "metadata" not in kw
        client.metadata["test.context"]["phase"] = "second"
        return reply

    client._request = request
    client.chat.completions.create(model="sent", messages=[])
    [event] = event_capture.read()
    assert event["metadata"] == {"test.context": {"phase": "first"}}


def test_mock_records_effective_parameters(event_capture):
    client = ChatClient("mock/model", seed=17, temperature=0.2, max_tokens=80)
    client.chat.completions.create(model="model", messages=[], max_tokens=100)
    event = event_capture.read()[0]
    assert event["call"]["request"] == {
        "model": "model", "messages": [],
        "seed": 17,
        "temperature": 0.2,
        "max_completion_tokens": 80,
    }
    assert event["metadata"] is None
    assert event["retries"] == 0


@pytest.mark.parametrize("model_id,needs_prefix", [
    ("azure/mistral-medium-3-5", True),
    ("azure/codestral-2501", True),
    ("azure/Ministral-3B", True),
    ("azureai/Mistral-large", True),
    ("mistral/mistral-small-latest", True),
    ("mistral/codestral-latest", True),
    ("azure/gpt-4.1", False),
    ("anthropic/claude-sonnet-4-5", False),
])
@pytest.mark.parametrize("last_role", ["assistant", "user"])
@pytest.mark.parametrize("mock", [False, True])
def test_mistral_prefill_flag_on_wire_and_mock(monkeypatch, event_capture, model_id, needs_prefix, last_role, mock):
    import httpx
    import openai

    provider_name, served_model = model_id.split("/", 1)
    provider = PROVIDERS[provider_name]
    monkeypatch.setenv(provider.api_key.name, "test-key")
    monkeypatch.setenv(provider.base_url.name, "https://example.invalid/v1")
    captured = []
    def transport(request):
        body = json.loads(request.content)
        captured.append(body)
        _, reply = client_and_reply()
        reply.model = body["model"]
        reply.choices[0].message.content = "Answer: 5"
        return httpx.Response(200, json=reply.model_dump(mode="json"))

    # Keep the real SDK serializer: only its HTTP transport is replaced.
    http_client = openai.DefaultHttpxClient
    monkeypatch.setattr(openai, "DefaultHttpxClient", lambda **kw: http_client(
        transport=httpx.MockTransport(transport), **kw,
    ))
    def respond(messages):
        captured.append({"messages": deepcopy(messages)})
        return "Answer: 5"

    client = ChatClient("mock/" + served_model if mock else model_id,
                        seed=42, max_tokens=64, mock_responder=respond)
    chat = [{"role": "user", "content": "Earlier question "},
            {"role": "assistant", "content": "Earlier answer "},
            {"role": "user", "content": "Choose a number \n"}]
    if last_role == "assistant":
        chat.append({"role": "assistant", "content": " Answer: \n\t"})
    original = deepcopy(chat)
    out = client.chat.completions.create(model=served_model, messages=chat, max_tokens=64)
    assert out.choices[0].message.content == "Answer: 5"
    [body] = captured
    expected = deepcopy(original)
    if last_role == "assistant" and needs_prefix:
        expected[-1]["prefix"] = True
    assert body["messages"] == expected
    assert "prefix" not in body
    assert chat == original
    [event] = event_capture.read()
    assert event["call"]["request"]["messages"] == expected


@pytest.mark.parametrize("content", [None, [{"type": "text", "text": "Answer: "}]])
def test_prefill_keeps_non_string_content(event_capture, content):
    client = ChatClient("mock/mistral-medium-3-5", mock_responder=lambda _: "5")
    chat = [{"role": "assistant", "content": content}]
    original = deepcopy(chat)
    client.chat.completions.create(model=client.served_model, messages=chat)
    [event] = event_capture.read()
    assert event["call"]["request"]["messages"] == [original[0] | {"prefix": True}]
    assert chat == original


@pytest.mark.parametrize("mock", [True, False])
@pytest.mark.parametrize("text,content,returned", [
    ("answer", "answer", "answer"),
    ("<think>reasoning</think>answer", [ContentReasoning(reasoning="reasoning"), ContentText(text="answer")], "answer"),
    ("<think>x</think>\n\nAnswer: 5", [ContentReasoning(reasoning="x"), ContentText(text="\n\nAnswer: 5")], "\n\nAnswer: 5"),
    (" \n<think> x </think>\n ", [ContentReasoning(reasoning=" x "), ContentText(text=" \n\n ")], " \n\n "),
    ("<think>truncated\n", "<think>truncated\n", "<think>truncated\n"),
    ('<think mode="deep">reasoning\ncontinued</think>answer',
     [ContentReasoning(reasoning="reasoning\ncontinued"), ContentText(text="answer")], "answer"),
    ('<think mode="deep">truncated', '<think mode="deep">truncated', '<think mode="deep">truncated'),
    ("<think>reasoning</think>", [ContentReasoning(reasoning="reasoning"), ContentText(text="")], ""),
    ("</think>answer", "</think>answer", "</think>answer"),
    # Unmatched closing tags remain literal text. The former stripper removed
    # every closing tag, including these mid-prose and quoted occurrences.
    pytest.param("Before </think> after", "Before </think> after", "Before </think> after",
                 id="unmatched-close-mid-prose"),
    pytest.param("</think>", "</think>", "</think>", id="unmatched-close-only"),
    pytest.param("answer</think>", "answer</think>", "answer</think>",
                 id="unmatched-close-at-end"),
    pytest.param("A</think>B</think>C", "A</think>B</think>C", "A</think>B</think>C",
                 id="repeated-unmatched-closes"),
    pytest.param("Use `</think>` to close it.", "Use `</think>` to close it.", "Use `</think>` to close it.",
                 id="quoted-close-in-prose"),
    pytest.param("x</think>\n\nAnswer: 5", "x</think>\n\nAnswer: 5", "x</think>\n\nAnswer: 5",
                 id="implicit-opening-is-not-inferred"),
    pytest.param("<think>x</think>\n\nAnswer: 5</think>",
                 [ContentReasoning(reasoning="x"), ContentText(text="\n\nAnswer: 5</think>")],
                 "\n\nAnswer: 5</think>", id="extra-close-after-reasoning"),
    pytest.param("Before </think> <think>x</think> after",
                 [ContentReasoning(reasoning="x"), ContentText(text="Before </think>  after")],
                 "Before </think>  after", id="unmatched-close-before-valid-block"),
    pytest.param("Before <think>x</think> after",
                 [ContentReasoning(reasoning="x"), ContentText(text="Before  after")],
                 "Before  after", id="text-on-both-sides-of-reasoning"),
    pytest.param("<think>x</think>answer<think>cutoff",
                 [ContentReasoning(reasoning="x"), ContentText(text="answer<think>cutoff")],
                 "answer<think>cutoff", id="closed-then-unclosed-block"),
    pytest.param("<think>Answer: 5.", "<think>Answer: 5.", "<think>Answer: 5.",
                 id="unclosed-think-at-start"),
    pytest.param("\n<think>Answer: 5.", "\n<think>Answer: 5.", "\n<think>Answer: 5.",
                 id="unclosed-think-after-newline"),
    pytest.param("Answer: <think>5.", "Answer: <think>5.", "Answer: <think>5.",
                 id="unclosed-think-in-middle"),
    pytest.param("Answer: 5.<think>", "Answer: 5.<think>", "Answer: 5.<think>",
                 id="unclosed-think-at-end"),
    pytest.param("<thinking>x</thinking>y", "<thinking>x</thinking>y", "<thinking>x</thinking>y",
                 id="different-tag-stays-literal"),
    pytest.param("a<think>x</think>b", [ContentReasoning(reasoning="x"), ContentText(text="ab")],
                 "ab", id="surrounding-text-concatenated"),
    pytest.param('<think signature="s">x</think>y', [ContentReasoning(reasoning="x"), ContentText(text="y")],
                 "y", id="think-with-attributes"),
    pytest.param("<think>x</think>a<think>y</think>b",
                 [ContentReasoning(reasoning="x"), ContentText(text="a<think>y</think>b")],
                 "a<think>y</think>b", id="only-first-block-extracted"),
    (" answer ", " answer ", " answer "),
    ("Rating: 9\n\n", "Rating: 9\n\n", "Rating: 9\n\n"),
])
def test_reasoning_parts_determine_completion_and_returned_text(event_capture, mock, text, content, returned):
    client, reply = client_and_reply()
    client.is_mock = mock
    client._mock_responder = lambda messages: text
    reply.choices[0].message.content = text
    client._request = lambda kw: reply
    result = client.chat.completions.create(model="served", messages=[])
    [event] = event_capture.read()
    assert result.choices[0].message.content == returned
    assert ChatMessageAssistant.model_validate(event["output"]["choices"][0]["message"]).content == content
    assert event["output"]["completion"] == ChatMessageAssistant(content=content).text
    assert event["metadata"] is None
    if not mock:
        assert event["call"]["response"]["choices"][0]["message"]["content"] == text


@pytest.mark.parametrize("text", ["answer", " answer ", None])
def test_provider_reasoning_content_is_preserved_separately(event_capture, text):
    client, reply = client_and_reply()
    data = reply.model_dump(mode="json")
    data["choices"][0]["message"].update(content=text, reasoning_content="Consider the evidence")
    reply = ChatCompletion.model_validate(data)
    original = reply.model_dump(mode="json")
    client._request = lambda kw: reply
    result = client.chat.completions.create(model="served", messages=[])
    [event] = event_capture.read()
    expected = [ContentReasoning(reasoning="Consider the evidence")]
    if text:
        expected.append(ContentText(text=text))
    assert ChatMessageAssistant.model_validate(event["output"]["choices"][0]["message"]).content == expected
    assert event["output"]["completion"] == (text or "")
    assert result.choices[0].message.content == (text or "")
    assert event["call"]["response"] == original
    assert event["metadata"] is None


@pytest.mark.parametrize("thinking", [None, True, False])
def test_qwen_reasoning_settings_are_caller_owned(event_capture, monkeypatch, thinking):
    _, reply = client_and_reply()
    reply.model = "Qwen3-test"
    sent = {}

    def create(**kw):
        sent.update(deepcopy(kw))
        return reply

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kw: SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create))),
    )
    client = ChatClient("openai/Qwen3-test")
    request = {
        "model": client.served_model,
        "messages": [{"role": "user", "content": "hello"}],
    }
    if thinking is not None:
        request["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": thinking, "custom": "kept"},
        }
    original = deepcopy(request)
    client.chat.completions.create(**request)

    assert sent == original
    assert request == original
    event = event_capture.read()[0]
    assert event["call"]["request"] == original


def test_tools_multimodal_input_all_choices_and_cached_usage(event_capture):
    client, reply = client_and_reply()
    data = reply.model_dump(mode="json")
    data["choices"] = [{
        "index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_content": "Consider the evidence",
            "tool_calls": [{"id": "tool-1", "type": "function", "function": {
                "name": "lookup", "arguments": '{"q":"hello"}',
            }}],
        }, "logprobs": {"content": [{"token": "lookup", "logprob": -0.5, "bytes": None, "top_logprobs": []}]},
    }, {"index": 1, "finish_reason": "length", "message": {"role": "assistant", "content": "Alternative"}}]
    data["usage"]["prompt_tokens_details"] = {"cached_tokens": 2}
    data["usage"]["completion_tokens_details"] = {"reasoning_tokens": 4}
    reply = ChatCompletion.model_validate(data)
    client._request = lambda kw: reply
    request = {
        "model": "served", "messages": [{"role": "developer", "content": "Be helpful"},
            {"role": "user", "content": [{"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "https://example.org/image.png", "detail": "low"}}]}],
        "tools": [{"type": "function", "function": {
            "name": "lookup", "description": "Find evidence", "parameters": {
                "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
            }, "strict": True,
        }}], "tool_choice": "required",
    }
    client.chat.completions.create(**request)
    event = event_capture.read()[0]
    assert event["input"][0]["role"] == "system"
    assert event["input"][1]["content"][1]["type"] == "image"
    assert event["tools"][0]["name"] == "lookup"
    assert event["tools"][0]["options"] == {"strict": True}
    assert event["tool_choice"] == "any"
    choices = event["output"]["choices"]
    assert choices[0]["message"]["content"][0]["type"] == "reasoning"
    assert choices[0]["message"]["tool_calls"][0]["arguments"] == {"q": "hello"}
    assert choices[0]["logprobs"]["content"][0]["token"] == "lookup"
    assert choices[1]["message"]["content"] == "Alternative"
    assert choices[1]["stop_reason"] == "max_tokens"
    assert event["output"]["usage"]["input_tokens"] == 1
    assert event["output"]["usage"]["input_tokens_cache_read"] == 2
    assert event["output"]["usage"]["reasoning_tokens"] == 4
    assert event["call"]["request"]["messages"] == request["messages"]
    assert event["call"]["response"]["choices"][0]["message"]["content"] is None


@pytest.mark.parametrize("arguments", ["not json", "[]"])
def test_malformed_tool_arguments_remain_observable(event_capture, arguments):
    client, reply = client_and_reply()
    data = reply.model_dump(mode="json")
    data["choices"][0]["message"]["tool_calls"] = [{
        "id": "bad", "type": "function", "function": {"name": "lookup", "arguments": arguments},
    }]
    client._request = lambda kw: ChatCompletion.model_validate(data)
    client.chat.completions.create(model="served", messages=[])
    event = event_capture.read()[0]
    assert event["output"]["choices"][0]["message"]["tool_calls"][0]["parse_error"]
    assert event["call"]["response"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == arguments
