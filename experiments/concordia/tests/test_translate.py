"""translate.py on hand-built data — pure, no Concordia import."""

import json

from concordia_sim.translate import emit_provenance, emit_scene, emit_summary, emit_turn


def _events(event_capture):
    return event_capture.read()


def test_scene_then_turns_stream_one_at_a_time(event_capture):
    roster = {"Alice", "Bob"}
    assert emit_scene("Alice and Bob meet at a cafe.") == 1
    # the action as given, with speech markers and quotes, is retained
    assert emit_turn(1, "Alice", 'Alice -- "Hi Bob!"', roster) == 1
    assert emit_turn(2, "Bob", "Bob Good to see you.", roster) == 1

    events = _events(event_capture)
    assert [e["type"] for e in events] == ["custom", "custom", "custom"]
    assert events[0]["data"]["narrator"] == "Game Master"
    assert events[0]["data"]["text"] == "Alice and Bob meet at a cafe."
    assert [e["data"]["actor"] for e in events[1:]] == ["Alice", "Bob"]
    assert events[1]["data"]["content"] == 'Alice -- "Hi Bob!"'
    assert events[2]["data"]["content"] == "Bob Good to see you."


def test_non_roster_and_empty_turns_dropped(event_capture):
    roster = {"Alice", "Bob"}
    assert emit_scene("") == 0  # no premise -> no scene message
    assert emit_turn(1, "(setup)", "...", roster) == 0  # setup phase, not a roster member
    assert emit_turn(2, "Alice", "Alice:   ", roster) == 0  # empty action
    assert emit_turn(3, "Bob", "Bob: hello", roster) == 1
    events = _events(event_capture)
    assert [e["data"]["actor"] for e in events] == ["Bob"]


def test_semantic_events_from_raw_log(event_capture):
    from concordia_sim.translate import TurnEmitter

    turns = TurnEmitter({"Alice", "Bob"})
    turns.raw_log.append({
        "Step": 1,
        "Summary": "noise",
        "Entity [Alice]": {
            "__observation__": {"Value": ["[observation] Bob waves."]},
            "SelfPerception": {"State": "Alice is warm.",
                               "Chain of thought": ["never emitted"]},
            "SituationPerception": {"State": "Alice is in a cafe."},
            "ConversationDynamics": {"State": "opening pleasantries"},
        },
        "Game Master --- Event: x": {"terminate": {"__act__": {"Value": "No"}}},
    })
    turns.drain()
    # rolling window: step 3 lists the old observation again plus one new one
    turns.raw_log.append({
        "Step": 3,
        "Entity [Alice]": {
            "__observation__": {"Value": ["[observation] Bob waves.",
                                          "[observation] Event: Alice -- \"hi\"",
                                          "[observation] Bob sits down."]},
            "SelfPerception": {"State": "Alice is curious."},
        },
    })
    turns.drain()
    events = _events(event_capture)

    obs = [e for e in events if e["type"] == "custom" and e["kind"] == "concordia.observation"]
    assert [(e["data"]["agent"], e["data"]["text"]) for e in obs] == [
        ("Alice", "Bob waves."), ("Alice", "Bob sits down.")]  # each one time only

    percs = [e for e in events if e.get("kind") == "concordia.perception"]
    assert [p["data"]["self"] for p in percs] == ["Alice is warm.", "Alice is curious."]
    assert percs[0]["data"]["situation"] == "Alice is in a cafe."
    # GM bookkeeping does not become a semantic event
    assert not any(e.get("agent") == "Game Master" for e in percs)


def test_provenance_is_an_agent_event(event_capture):
    emit_provenance(concordia_version="2.4.0", model="mock/model",
                    agents=2, python_version="3.13.0")
    (event,) = _events(event_capture)
    assert event["type"] == "custom"
    assert event["kind"] == "concordia.provenance"
    assert event["data"]["concordia"] == "2.4.0"
    assert event["data"]["agents"] == 2


def test_summary_emits_metrics_and_returns_dict(event_capture):
    summary = emit_summary(steps=3, agents=2,
                           world_events=9, model_calls=14)
    assert summary == {"steps": 3, "agents": 2,
                       "world_events": 9, "model_calls": 14}
    events = _events(event_capture)
    assert {e["type"] for e in events} == {"result"}
    assert {e["name"]: e["value"] for e in events} == summary
