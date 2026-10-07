"""Each task runs the authors' code end to end on the mock path."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError
from reps_events.testing import assert_conformant
from reps_testing import assert_run_has_no_secrets
from conftest import events_of, results_of

from agentsnet_adapter.main import Params, run
from agentsnet_adapter.models import Payload

CELL = dict(model="mock/model", task="coloring", graph_generator="ws", graph_size=4, graph_index=0, rounds=2,
            chain_of_thought=True)


def _typed(events):
    assert_conformant(events)
    return [TypeAdapter(Payload).validate_python(event) for event in events]


def test_coloring_ws4(run_dir, event_capture):
    run(Params(**CELL))
    events = event_capture.read()
    r = results_of(events)
    assert r["successful"] is True and r["rounds_run"] == 2
    assert 0.0 <= r["score"] <= 1.0 and r["solved"] == (r["score"] == 1.0)
    assert r["fallbacks"] == 0 and r["unparsed_messages"] == 0 and r["unparsed_answers"] == 0
    # ws/4/0 is the complete graph on 4 nodes: every agent messages 3 neighbours in each of 2 rounds
    assert r["messages_sent"] == 4 * 3 * 2
    calls = events_of(events, "llm.call")
    # 4 agents x (bootstrap + rounds), the last round being the final answer; every call attributed to a named agent
    assert len(calls) == 4 * (1 + 2)
    assert {c["agent"] for c in calls} == {"Jason", "Sara", "Marilyn", "Samantha"}
    assert all(c["model"] == "mock/model" and c["input"] for c in calls)
    # the final call of every agent carries the agent's whole conversation (langgraph's checkpointer)
    assert max(len(c["input"]) for c in calls) == 2 + 1 + 2 * 1 + 1  # system, ask, reply, (round, reply) x 1, final ask
    messages = events_of(events, "custom", "agentsnet.message")
    assert len(messages) == 24 and all(m["data"]["delivered"] for m in messages)
    assert {m["data"]["round"] for m in messages} == {1, 2}
    answers = events_of(events, "custom", "agentsnet.answer")
    assert len(answers) == 4 and all(a["data"]["valid"] for a in answers)
    assert all(a["data"]["answer"].startswith("Group ") for a in answers)
    graph = events_of(events, "custom", "agentsnet.graph")[0]["data"]
    assert graph["num_nodes"] == 4 and graph["num_edges"] == 6 and graph["diameter"] == 1 and graph["graph_seed"] == 410
    config = events_of(events, "custom", "agentsnet.config")[0]["data"]
    assert config["seed"] == 7 and config["provider"] == "mock" and config["rounds"] == 2
    assert config["upstream_class"] == "LiteralMessagePassing.Coloring"
    assert len(config["dataset_sha256"]) == 64
    marker = events_of(events, "custom", "agentsnet.upstream_record")[0]["data"]
    assert marker["source"].startswith("results/coloring_results_") and marker["bytes"] > 0
    record = json.loads((run_dir / "artifacts" / "upstream_record.json").read_text())
    assert record["task"] == "coloring" and record["successful"] is True and record["score"] == r["score"]
    assert record["num_nodes"] == 4 and set(record["transcripts"]) == {"Jason", "Sara", "Marilyn", "Samantha"}
    assert list((run_dir / "upstream_work" / "results").glob("*.json"))
    _typed(events)
    assert_run_has_no_secrets(run_dir)
    # the import-time patches: no throttling, and the ollama branch's env read is satisfied
    import os
    import LiteralMessagePassing as lmp
    assert os.environ["OLLAMA_URI"] and lmp.InMemoryRateLimiter(**lmp.RATE_LIMITER_KWARGS["anything"]) is None


def test_consensus_rounds_from_diameter(run_dir, event_capture):
    # upstream determine_rounds: consensus runs 2 * diameter + 1 rounds whatever --rounds says
    run(Params(**CELL | dict(task="consensus", graph_generator="ba", graph_size=8, graph_index=1, rounds=99)))
    events = event_capture.read()
    r = results_of(events)
    graph = events_of(events, "custom", "agentsnet.graph")[0]["data"]
    assert r["rounds_run"] == 2 * graph["diameter"] + 1
    assert r["successful"] is True and r["score"] in (0.0, 1.0)
    answers = {a["data"]["agent"]: a["data"]["answer"] for a in events_of(events, "custom", "agentsnet.answer")}
    assert set(answers.values()) <= {"0", "1"}
    assert r["score"] == (1.0 if len(set(answers.values())) == 1 else 0.0)
    _typed(events)


def test_leader_election_and_matching(run_dir, event_capture):
    run(Params(**CELL | dict(task="leader_election", graph_generator="dt", graph_size=4, graph_index=2)))
    events = event_capture.read()
    r = results_of(events)
    answers = [a["data"]["answer"] for a in events_of(events, "custom", "agentsnet.answer")]
    assert r["score"] == (1.0 if answers.count("Yes") == 1 else 0.0)
    assert len(events_of(events, "llm.call")) == 4 * (1 + r["rounds_run"])
    _typed(events)


def test_matching_valid_answers_are_neighbours(run_dir, event_capture):
    run(Params(**CELL | dict(task="matching", rounds=1)))
    events = event_capture.read()
    r = results_of(events)
    assert r["rounds_run"] == 1 and 0.0 <= r["score"] <= 1.0
    for a in events_of(events, "custom", "agentsnet.answer"):
        assert a["data"]["valid"]
    _typed(events)


def test_vertex_cover(run_dir, event_capture):
    # seed 7: at least one agent picks "Yes", so upstream's scorer does not divide by zero
    run(Params(**CELL | dict(task="vertex_cover", graph_generator="ba", graph_size=4, graph_index=0, rounds=1)))
    r = results_of(event_capture.read())
    assert r["successful"] is True and 0.0 <= r["score"] <= 1.0


def test_missing_instance_fails_before_any_call(run_dir, event_capture):
    with pytest.raises(ValueError, match="Graph not found"):
        run(Params(**CELL | dict(graph_size=5)))
    events = event_capture.read()
    assert not events_of(events, "llm.call") and not events_of(events, "result")


def test_no_chain_of_thought_prompt(run_dir, event_capture):
    run(Params(**CELL | dict(chain_of_thought=False, rounds=1)))
    calls = events_of(event_capture.read(), "llm.call")
    prompts = "\n".join(str(part) for c in calls for part in c["input"])
    assert "Elaborate your chain of thought" not in prompts


@pytest.mark.parametrize("bad", [dict(task="sorting"), dict(graph_generator="er"), dict(graph_size=0),
                                 dict(graph_index=-1), dict(rounds=0)])
def test_invalid_params(bad):
    with pytest.raises(ValidationError):
        Params(**CELL | bad)


def test_unparseable_agent_still_scores(run_dir, event_capture, monkeypatch):
    """One agent never writes JSON messages or a parseable final answer: upstream
    re-prompts it, silences it, scores its answer as None, and the run completes."""
    import agentsnet_adapter.main as adapter
    from agentsnet_adapter.mock import mock_responder

    def garbling(seed):
        respond = mock_responder(seed)
        def reply(messages):
            system = "\n".join(str(m.get("content")) for m in messages if m.get("role") == "system")
            return "I would rather not say." if "Your name is Jason." in system else respond(messages)
        return reply
    monkeypatch.setattr(adapter, "mock_responder", garbling)
    run(Params(**CELL))
    events = event_capture.read()
    r = results_of(events)
    assert r["successful"] is True and r["score"] == 0.0 and r["solved"] is False   # coloring: a None answer scores 0
    assert r["unparsed_answers"] == 1 and r["unparsed_messages"] == 2 and r["fallbacks"] == 3
    assert r["messages_sent"] == 3 * 3 * 2                                       # Jason's rounds deliver nothing
    answers = {a["data"]["agent"]: a["data"] for a in events_of(events, "custom", "agentsnet.answer")}
    assert answers["Jason"].get("answer") is None and answers["Jason"]["valid"] is False   # omitted on the wire
    assert all(a["valid"] for name, a in answers.items() if name != "Jason")
    typed = {e.data.agent: e.data for e in _typed(events) if getattr(e, "kind", None) == "agentsnet.answer"}
    assert typed["Jason"].answer is None
    record = json.loads((run_dir / "artifacts" / "upstream_record.json").read_text())
    assert record["answers"].count(None) == 1 and record["score"] == 0.0
