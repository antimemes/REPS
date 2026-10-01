import json

from adb_events import read_events
from adb_runner.card import derive_card
from adb_runner import protocol
from test_protocol import MANIFEST, run_fixture


def test_card_is_exact_copies_plus_stream_projection(tmp_path):
    _, wire, store = run_fixture(tmp_path, fetch_ref="github:owner/repo/abc", tree_hash="sha256-tree")
    card = json.loads((store.dir / "run.json").read_text())
    rebuilt = derive_card(read_events(store.dir))
    assert set(card) == {"identity", "inputs", "lifecycle", "provenance", "definitions", "derived"}
    assert card["derived"] == rebuilt["derived"]
    assert card["derived"]["results"] == {"m": 42}
    assert card["derived"]["usage"] == {"input_tokens": 3, "output_tokens": 5}
    assert card["derived"]["counts"]["llm_calls"] == 1
    assert set(card["derived"]["counts"]) == {"llm_calls", "failed_calls", "by_kind", "llm_calls_by_agent"}
    assert card["derived"]["last_seq"] == wire[-1]["seq"]
    assert card["derived"]["last_event_at"] == wire[-1]["ts"]
    assert card["derived"]["counts"]["llm_calls_by_agent"] == {"a": 1}
    assert "by_actor" not in card["derived"]["counts"]
    start, end = wire[0], wire[-1]
    assert card["identity"] == {"run": start["run"], "condition": start["event"]["condition"],
                                "experiment": start["experiment"], "schema": start["schema"]}
    assert card["inputs"] == {key: start["event"][key] for key in ("params", "seed")}
    assert card["provenance"] == {key: start["event"][key] for key in ("source", "fetch_ref", "tree_hash", "runtime")}
    assert card["definitions"] == {"results": start["event"]["result_definitions"]}
    assert card["lifecycle"] == {
        "started_at": start["ts"], "finished_at": end["ts"],
        **{k: end["event"][k] for k in ("state", "exit_code", "duration_s")},
    }


def test_usage_includes_input_cache_reads_and_writes(tmp_path):
    script = '''#!/bin/sh
adb-emit llm-call --model mock/x --input '[]' --output '{"usage":{"input_tokens":3,"input_tokens_cache_read":4,"input_tokens_cache_write":2,"output_tokens":5}}' </dev/null
adb-emit llm-call --model mock/x --input '[]' --output '{"usage":{"input_tokens":7,"input_tokens_cache_read":11,"input_tokens_cache_write":13,"output_tokens":17}}' </dev/null
adb-emit llm-call --model mock/x --input '[]' --output '{}' </dev/null
adb-emit custom --kind t.usage --data '{"usage":{"input_tokens":99999}}'
'''
    result, _, store = run_fixture(tmp_path, script=script)
    assert result.state == "completed"
    card = json.loads((store.dir / "run.json").read_text())
    # (3 + 4 + 2) + (7 + 11 + 13): only recorded model-call usage counts.
    expected = {"input_tokens": 40, "output_tokens": 22}
    assert card["derived"]["usage"] == expected
    assert derive_card(read_events(store.dir))["derived"]["usage"] == expected


def test_agent_counts_use_only_model_calls_without_loading_schema(tmp_path):
    # A consumer-only schema export need not be available on the launching machine.
    manifest = {**MANIFEST, "schema": {"version": 0, "models": "unused:Payload",
                                     "path": str(tmp_path / "missing-schema.json")}}
    script = '''#!/bin/sh
adb-emit custom --kind t.message --data '{"agent":"a","text":"hi"}'
adb-emit custom --kind t.message --data '{"agent":"observer","text":"hi"}'
adb-emit llm-call --model mock/x --agent a --input '[]' --output '{"model":"x-z"}' </dev/null
adb-emit llm-call --model mock/x --agent a --input '[]' --output '{}' --error offline </dev/null
adb-emit llm-call --model mock/x --agent b --input '[]' --output '{"model":"x-a"}' </dev/null
adb-emit llm-call --model mock/x --input '[]' --output '{"model":"x-z"}' </dev/null
'''
    result, _, store = run_fixture(tmp_path, script=script, manifest=manifest)
    assert result.state == "completed"
    derived = json.loads((store.dir / "run.json").read_text())["derived"]
    assert derived == derive_card(read_events(store.dir))["derived"]
    assert derived["counts"]["llm_calls"] == 4
    assert derived["counts"]["failed_calls"] == 1
    assert derived["counts"]["llm_calls_by_agent"] == {"a": 2, "b": 1}
    assert derived["counts"]["by_kind"]["t.message"] == 2
    assert derived["served_models"] == ["x-a", "x-z"]
    assert "by_actor" not in derived["counts"]


def test_live_heartbeat_refreshes_derived_without_reading_card_as_input(tmp_path, monkeypatch):
    monkeypatch.setattr(protocol, "HEARTBEAT_S", 0.05)
    snapshots = []
    mtimes = []
    original = protocol.RunStore.write_run_json

    def observe(store, card):
        original(store, card)
        snapshots.append(card)
        mtimes.append((store.dir / "run.json").stat().st_mtime_ns)
        assert "heartbeat_at" not in card["lifecycle"]
        if card["lifecycle"]["state"] == "running" and card["derived"]["results"]:
            assert card["derived"] == derive_card(read_events(store.dir))["derived"]
            # Deliberate cache corruption cannot become an input to the next beat.
            (store.dir / "run.json").write_text('{"inputs":{"params":{"x":999}}}')

    monkeypatch.setattr(protocol.RunStore, "write_run_json", observe)
    _, _, store = run_fixture(tmp_path, script='#!/bin/sh\nadb-emit result --name m --value 7\nexec >/dev/null 2>&1\nsleep 0.8\n')
    live = [c for c in snapshots if c["lifecycle"]["state"] == "running" and c["derived"]["results"]]
    assert live and live[-1]["derived"]["results"] == {"m": 7}
    assert len(set(mtimes)) > 2
    card = json.loads((store.dir / "run.json").read_text())
    assert card["inputs"]["params"] == {"x": 1}
    assert card["derived"] == derive_card(read_events(store.dir))["derived"]
