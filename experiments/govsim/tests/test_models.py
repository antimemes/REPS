import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from reps_events import emit, export_schema
from reps_events.render import hint_path_declared, hint_paths
from govsim_adapter.models import GovsimConfig, GovsimState, Payload, StateData, ACTION_MODELS


def test_union_includes_every_shared_tag():
    from reps_events import EVENT_MODELS

    assert set(TypeAdapter(Payload).json_schema()["discriminator"]["mapping"]) == set(EVENT_MODELS)


def test_config_hint_exports_the_persona_registry():
    hint = export_schema(Payload)["$defs"]["GovsimConfig"]["x-reps-render"]
    assert hint["actor_registry"] == {"path": "data.experiment.personas", "label": "name"}
    event = GovsimConfig.model_validate({"data": {
        "experiment": {"personas": {"persona_0": {"name": "John"}, "persona_1": {"name": "Kate"}, "num": 2}},
        "code_version": "0", "group_name": "test", "llm": {}, "mix_llm": [], "seed": 0, "debug": True,
    }})
    registry = event.model_dump()
    for part in hint["actor_registry"]["path"].split("."):
        registry = registry[part]
    assert registry["persona_1"][hint["actor_registry"]["label"]] == "Kate"


@pytest.mark.parametrize("kind,data", [
    ("config", {"experiment": {}, "code_version": "0", "group_name": "test",
                "llm": {}, "mix_llm": [], "seed": 0, "debug": True}),
    ("state", {"round": 0, "collected": {}, "final": False}),
    ("unparsed_log", {"text": "unfinished"}),
    ("upstream_log", {"source": "persona_0/nodes.json", "bytes": 2, "sha256": "0" * 64, "records": 0}),
    ("memory", {"persona": "persona_0", "node": {"description": "Keep some fish.", "unknown": None}}),
    ("record", {"action": "future", "nested": {"values": [1, None]}}),
])
def test_custom_union_has_typed_data_and_round_trips(kind, data):
    wire = json.dumps({"type": "custom", "kind": f"govsim.{kind}", "data": data})
    adapter = TypeAdapter(Payload)
    parsed = adapter.validate_json(wire)
    assert isinstance(parsed.data, BaseModel)
    assert parsed.data.model_dump(exclude_none=True) == data
    assert adapter.validate_json(parsed.model_dump_json(exclude_none=True)) == parsed


@pytest.mark.parametrize("kind,data", [
    ("future", {}),
    ("state", {"round": True, "collected": {}, "final": False}),
    ("state", {"round": 0, "resource": "1", "collected": {}, "final": False}),
    ("pool", {"round": 0, "pool": 1}),
    ("upstream_log", {"source": "x", "bytes": -1, "sha256": "0" * 64, "records": 0}),
    ("upstream_log", {"source": "x", "bytes": 2, "sha256": "invalid", "records": 0}),
    ("upstream_log", {"source": "x", "bytes": 2, "sha256": "0" * 64, "records": True}),
    ("memory", {"persona": 1, "node": {}}),
    ("memory", {"persona": "persona_0", "node": [], "extra": 0}),
    ("replay_started", {"source": "log_env.json"}),
    ("round_started", {"round": 0}),
    ("replay_finished", {}),
])
def test_custom_union_rejects_unknown_kinds_and_invalid_data(kind, data):
    with pytest.raises(ValidationError):
        TypeAdapter(Payload).validate_json(json.dumps(
            {"type": "custom", "kind": f"govsim.{kind}", "data": data},
        ))


def test_typed_custom_emits_shared_wire_container(event_capture):
    event = GovsimState(data=StateData(round=0, resource=75, collected={}, final=False))
    emit(event)
    [wire] = event_capture.read()
    assert TypeAdapter(Payload).validate_python(wire) == event


def test_emit_validates_custom_subclass_constraints(event_capture):
    event = GovsimState.model_construct(kind="govsim.future", data=StateData(round=0, resource=75, collected={}, final=False))
    with pytest.raises(ValidationError):
        emit(event)
    assert event_capture.read() == []


@pytest.mark.parametrize("fixture", [".", "mock_storage"])
def test_real_upstream_rows_keep_data_and_have_resolvable_hints(event_capture, fixture):
    from govsim_adapter.upstream import ingest_storage

    storage = Path(__file__).parent / "fixtures" / fixture
    rows = json.loads((storage / "log_env.json").read_text())
    assert {row["action"] for row in rows} == set(ACTION_MODELS)
    assert ingest_storage(storage) == rows
    events = [TypeAdapter(Payload).validate_python(e) for e in event_capture.read()]
    replayed = [e for e in events if e.kind in {m.model_fields["kind"].default for m in ACTION_MODELS.values()}]
    assert [e.data.root for e in replayed] == rows
    markers = [e for e in events if e.kind == "govsim.upstream_log"]
    node_files = sorted(storage.glob("persona_*/nodes.json"))
    assert [e.data.source for e in markers] == [
        "log_env.json", *(path.relative_to(storage).as_posix() for path in node_files),
    ]
    for marker in markers:
        raw = (storage / marker.data.source).read_bytes()
        assert marker.data.bytes == len(raw)
        assert marker.data.sha256 == hashlib.sha256(raw).hexdigest()
        assert marker.data.records == len(json.loads(raw))
    memories = [e for e in events if e.kind == "govsim.memory"]
    assert [e.data.node.root for e in memories] == [
        node for path in node_files for node in json.loads(path.read_text())
    ]
    assert {e.data.persona for e in memories} == {path.parent.name for path in node_files}
    if fixture == "mock_storage":
        assert len(node_files) == 5
    for marker in markers:
        start = events.index(marker) + 1
        following = events[start:start + marker.data.records]
        if marker.data.source != "log_env.json":
            persona = Path(marker.data.source).parent.name
            assert all(e.kind == "govsim.memory" and e.data.persona == persona for e in following)
    for event in events:
        hint = event.render
        assert hint is not None
        if event.kind in {"govsim.harvest", "govsim.resource_limit"}:
            assert {path.removeprefix("data.") for path in hint.fields} == set(event.data.root)
        for path in hint_paths(hint):
            if path:
                value = event.model_dump(mode="json")
                for part in path.split("."):
                    assert part in value, (event.kind, path)
                    value = value[part]


def test_govsim_hint_paths_are_declared_for_typed_data():
    root = export_schema(Payload)
    assert "GovsimPool" not in root["$defs"]
    assert "PoolData" not in root["$defs"]
    assert "govsim.pool" not in json.dumps(root)
    for name, schema in root["$defs"].items():
        data = schema.get("properties", {}).get("data", {})
        if data.get("$ref", "").endswith("/RecordData"):
            # Open native rows have no declared fields; the real log fixture
            # above checks every one of their paths against actual evidence.
            continue
        from reps_events import RenderHint
        hint = RenderHint.model_validate(schema["x-reps-render"]) if "x-reps-render" in schema else None
        if hint is None:
            continue
        for path in hint_paths(hint):
            if name == "GovsimMemory" and path.startswith("data.node."):
                # Node fields stay open; real fixtures above validate these paths.
                continue
            assert hint_path_declared(schema, path, root), (name, path)
