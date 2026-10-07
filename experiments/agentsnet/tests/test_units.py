"""The adapter's own pieces, without running upstream."""

from __future__ import annotations

import json
import re

from agentsnet_adapter import results, upstream
from agentsnet_adapter.mock import mock_responder

SYSTEM = ("You are an agent that is connected with other agents (your neighbors), who you communicate with. "
          "Your task is to partition yourselves into groups.\nThe rules are as follows:\n"
          "1. There are 4 agents in total. Everybody has a unique name. Your name is Jason.\n"
          "2. You can only communicate with your immediate neighbors (Sara, Marilyn, Samantha). You cannot see ...")


def test_staged_tree_is_the_authors_code(run_dir):
    work = upstream.stage(run_dir)
    for entry in ("main.py", "LiteralMessagePassing.py", "utils.py", "LICENSE"):
        assert (work / entry).is_file()
    assert not list(work.rglob("__pycache__")) and not (work / "results").exists()
    rev = upstream.upstream_rev()
    assert rev is None or re.fullmatch(r"[0-9a-f]{40}", rev)
    assert re.fullmatch(r"[0-9a-f]{64}", upstream.sha256_of(upstream.dataset_path()))


def test_mock_messages_every_neighbour():
    respond = mock_responder(7)
    text = respond([{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "What are the first messages you want to send to your neighbors?"}])
    body = json.loads(text[text.index("{"):text.rindex("}") + 1])
    assert set(body) == {"Sara", "Marilyn", "Samantha"} and all(isinstance(v, str) for v in body.values())
    text = respond([{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "These are the messages from your neighbors:\n\n... Your neighbors are: Sara, Marilyn, Samantha "
                                                "These are the last messages that your neighbors will receive from you."}])
    body = json.loads(text[text.index("{"):text.rindex("}") + 1])
    assert set(body) == {"Sara", "Marilyn", "Samantha"}


def test_mock_final_answer_is_a_valid_option_and_deterministic():
    final = ("Message passing has finished, here are the last messages you got from your neighbors:\n\n"
             "Which group do you assign yourself to? Format your answer as follows: '### Final Answer ###', followed by "
             "your final answer. Don't use any text for your final answer except one of these valid options: Group 1, Group 2, Group 3, Group 4")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": final}]
    first = mock_responder(7)(messages)
    assert first == mock_responder(7)(messages)
    answer = re.search(r"### Final Answer ###\s*(Group \d)", first).group(1)
    assert answer in {"Group 1", "Group 2", "Group 3", "Group 4"}


def test_round_messages_follow_upstream_prompts():
    def parse(text):
        try:
            return json.loads(text)
        except ValueError:
            return None
    history = [
        {"type": "system", "data": {"content": SYSTEM}},
        {"type": "human", "data": {"content": "What are the first messages you want to send to your neighbors? ..."}},
        {"type": "ai", "data": {"content": '{"Sara": "hi"}'}},
        {"type": "human", "data": {"content": "These are the messages from your neighbors:\n\n..."}},
        {"type": "ai", "data": {"content": "not json"}},
        {"type": "human", "data": {"content": "Your messages could not be parsed into JSON. Please check your response and try again."}},
        {"type": "ai", "data": {"content": '{"Marilyn": "second try"}'}},
        {"type": "human", "data": {"content": "These are the messages from your neighbors:\n\n..."}},
        {"type": "ai", "data": {"content": "still not json"}},
        {"type": "human", "data": {"content": "Your messages could not be parsed into JSON. Please check your response and try again."}},
        {"type": "ai", "data": {"content": "nope"}},
        {"type": "human", "data": {"content": "Message passing has finished, here are the last messages ..."}},
        {"type": "ai", "data": {"content": "### Final Answer ###\nGroup 1"}},
    ]
    assert results.round_messages(history, parse) == [{"Sara": "hi"}, {"Marilyn": "second try"}, {}]


def test_summary_drops_null_score():
    class Model:
        num_fallbacks = [1, 0]
        num_failed_json_parsings_after_retry = [0, 0]
        num_failed_answer_parsings_after_retry = [0, 1]
    assert results.summary({"successful": False, "score": None}, Model(), 3) == {
        "successful": False, "rounds_run": 3, "fallbacks": 1, "unparsed_messages": 0, "unparsed_answers": 1}
    half = results.summary({"successful": True, "score": 0.5}, Model(), 3)
    assert half["score"] == 0.5 and half["solved"] is False
    assert results.summary({"successful": True, "score": 1.0}, Model(), 3)["solved"] is True


def test_real_replies_carry_langchain_metadata(monkeypatch):
    """A real SDK response goes through langchain-openai's own result builder, so
    upstream's transcripts get the usage_metadata and response_metadata ChatOpenAI
    would have written."""
    import types
    from openai.types.chat import ChatCompletion
    from langchain_core.messages import HumanMessage

    from agentsnet_adapter.llm import RepsChatModel

    completion = ChatCompletion.model_validate({
        "id": "chatcmpl-x", "object": "chat.completion", "created": 1, "model": "gpt-4.1-mini-2025-04-14",
        "system_fingerprint": "fp_1",
        "choices": [{"index": 0, "finish_reason": "stop", "logprobs": None,
                     "message": {"role": "assistant", "content": '{"Sara": "hi"}'}}],
        "usage": {"prompt_tokens": 505, "completion_tokens": 200, "total_tokens": 705,
                  "prompt_tokens_details": {"cached_tokens": 0}, "completion_tokens_details": {"reasoning_tokens": 0}},
    })
    client = types.SimpleNamespace(is_mock=False, served_model="gpt-4.1-mini",
                                   chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **kw: completion)))
    model = RepsChatModel(model_id="azure/gpt-4.1-mini", run_seed=7, agent_names={})
    monkeypatch.setattr(RepsChatModel, "client_for", lambda self, agent: client)   # pydantic forbids instance patching
    result = model._generate([HumanMessage(content="q")])
    message = result.generations[0].message
    assert message.content == '{"Sara": "hi"}'
    assert message.usage_metadata["input_tokens"] == 505 and message.usage_metadata["output_tokens"] == 200
    assert result.generations[0].generation_info["finish_reason"] == "stop"
    assert result.llm_output["token_usage"] == completion.usage.model_dump()
    assert (result.llm_output["model_name"], result.llm_output["system_fingerprint"], result.llm_output["id"]) == (
        "gpt-4.1-mini-2025-04-14", "fp_1", "chatcmpl-x")
