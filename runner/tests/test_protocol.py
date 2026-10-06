"""The runner protocol, exercised against a real subprocess — a fixture script speaking
the contract (params on stdin, event payloads on a Unix socket). This is the seam every
experiment in any language crosses; no mocks, the fixture IS a minimal experiment."""

import json
import os
import stat
import shlex
import sys

import pytest

from adb_events import VOCABULARY_VERSION
from adb_runner.protocol import execute_run
from adb_runner.store import RunStore

MANIFEST = {
    "name": "t",
    "v": VOCABULARY_VERSION,
    "params": {"x": {"type": {"kind": "int"}, "default": 1}},
    "results": [
        {
            "name": "m",
            "type": {"kind": "int"},
            "label": "Measurement",
            "description": "The measured count.",
            "unit": "items",
            "details": "Count recorded by the fixture.",
        },
        {"name": "missing", "type": {"kind": "float"}},
    ],
}

FIXTURE = r"""#!/bin/sh
read -r params
adb-emit status --detail "got $params"
echo '{"type":"status","phase":"experiment-stage"}'
adb-emit result --name m --value 42
adb-emit result --name undeclared --value 1
adb-emit custom --kind govsim.discussion --data '{"speaker":"a","text":"hi"}'
adb-emit llm-call --agent a --model mock/x <<'EOF'
{"input":[{"role":"user","content":"q"}],"output":{"choices":[{"message":{"role":"assistant","content":"r"}}],"usage":{"input_tokens":3,"output_tokens":5}}}
EOF
echo '{"type":"run.end","fake":"reserved"}'
echo '{"type":"t.custom","anything":1}'
echo '{"no_type":"opaque blob"}'
echo '[1, 2, 3]'
echo 'not json at all'
echo "to stderr" >&2
exit 0
"""


def run_fixture(tmp_path, script=FIXTURE, params=None, manifest=None, on_event=None,
                credential_env=None, fetch_ref=None, tree_hash=None):
    prog = tmp_path / "exp.sh"
    # Mirror runtimeInputs: adb-emit is a PATH executable, not a shell function.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "adb-emit"
    cli.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m adb_events.cli "$@"\n')
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    prog.write_text(script)
    prog.chmod(prog.stat().st_mode | stat.S_IEXEC)
    store = RunStore(tmp_path / "home", "cid", "20260916t120000z-012345abcdef",
                     experiment=(manifest or MANIFEST)["name"])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
        result = execute_run(
            program=str(prog),
            manifest=MANIFEST if manifest is None else manifest,
            params=params or {"x": 1},
            condition_id="cid",
            source="dirty:test",
            fetch_ref=fetch_ref,
            tree_hash=tree_hash,
            seed=42,
            store=store,
            run_id="20260916t120000z-012345abcdef",
            on_event=on_event,
            credential_env=credential_env,
        )
    envelopes = [
        json.loads(line)
        for f in sorted(store.dir.glob("events.jsonl"))
        for line in f.read_text().splitlines()
    ]
    return result, envelopes, store


def test_protocol_end_to_end(tmp_path):
    result, envelopes, store = run_fixture(tmp_path)

    assert result.state == "completed"
    # transport envelope: runner owns v/ts/run/seq; the payload rides under `event`
    assert [e["seq"] for e in envelopes] == list(range(len(envelopes)))
    assert all(e["v"] == VOCABULARY_VERSION and e["run"] == "20260916t120000z-012345abcdef" and "event" in e for e in envelopes)
    assert all(len(e["ts"]) == 27 and e["ts"].endswith("Z") for e in envelopes)
    assert all(e["experiment"] == "t" and e["schema"] == 0 for e in envelopes)
    payloads = [e["event"] for e in envelopes]

    record = json.loads((store.dir / "run.json").read_text())
    assert record["lifecycle"]["state"] == "completed" and "phase" not in record
    assert "run.status" not in {p["type"] for p in payloads}
    assert {
        "type": "stdout",
        "line": '{"type":"status","phase":"experiment-stage"}',
    } in payloads
    assert payloads[-1]["state"] == "completed" and "phase" not in payloads[-1]

    start = payloads[0]
    assert start["type"] == "run.start"
    assert start["params"] == {"x": 1}
    assert "dirty" not in start and "experiment" not in start
    assert "fetch_ref" not in start and "tree_hash" not in start
    assert len(record["lifecycle"]["started_at"]) == 27 and record["lifecycle"]["started_at"].endswith("Z")
    assert start["result_definitions"] == MANIFEST["results"]
    assert record["definitions"]["results"] == MANIFEST["results"]

    end = payloads[-1]
    assert end["type"] == "run.end" and "fake" not in end  # the runner's own, last
    assert set(end) == {"type", "state", "duration_s", "exit_code"}
    assert end["exit_code"] == record["lifecycle"]["exit_code"] == 0
    assert not {"summary", "usage_totals", "base_seed", "replicates", "realized_params", "spec_params"} & record.keys()
    assert not hasattr(result, "summary") and not hasattr(result, "usage")

    by_type = {}
    for p in payloads:
        by_type.setdefault(p.get("type"), []).append(p)
    # Even event-shaped JSON printed to stdout remains text.
    assert len(by_type["run.end"]) == 1
    assert "t.custom" not in by_type and None not in by_type
    # Absent optional fields are omitted throughout the saved model call.
    assert "null" not in json.dumps(by_type["llm.call"][0])
    stdout_lines = [p["line"] for p in by_type["stdout"]]
    assert '{"type":"run.end","fake":"reserved"}' in stdout_lines
    assert '{"no_type":"opaque blob"}' in stdout_lines
    assert "[1, 2, 3]" in stdout_lines and "not json at all" in stdout_lines
    from adb_events import read_events

    records = list(read_events(store.dir))
    assert [record.model_dump(mode="json", exclude_none=True) for record in records] == envelopes
    assert not (store.dir / "schema.json").exists()
    assert not (store.dir / "shared-schema.json").exists()
    # - stderr → stderr events, no invented level
    assert [p["line"] for p in by_type["stderr"]] == ["to stderr"]

    # no views are materialized: projections are rendered on demand —
    # the deposit carries irreducibles only
    assert not (store.dir / "artifacts").exists()
    assert "artifact" not in by_type


def test_envelope_payload_schema_is_separate_from_manifest_version(tmp_path):
    manifest = {**MANIFEST, "schema_version": 12,
                "schema": {"version": 7, "models": "test_models:Payload"}}
    _, envelopes, store = run_fixture(tmp_path, script="#!/bin/sh\n", manifest=manifest)
    assert all(e["experiment"] == "t" and e["schema"] == 7 for e in envelopes)
    assert json.loads((store.dir / "run.json").read_text())["identity"]["schema"] == 7


def test_run_start_records_the_parameters_passed_to_the_child(tmp_path):
    _, envelopes, _ = run_fixture(tmp_path, script="#!/bin/sh\n", params={"x": 2, "optional": None})
    start = envelopes[0]["event"]
    assert start["params"] == {"x": 2, "optional": None}
    assert start["condition"] == "cid"
    assert start["source"] == "dirty:test"
    assert "fetch_ref" not in start
    assert "replicate" not in start
    assert start["seed"] == 42


@pytest.mark.parametrize("results", [None, []])
def test_no_declarations_preserves_results_and_warns(tmp_path, results):
    manifest = {"v": VOCABULARY_VERSION, "name": "t", "params": MANIFEST["params"]}
    if results is not None:
        manifest["results"] = results
    result, envelopes, store = run_fixture(tmp_path, manifest=manifest)
    assert not hasattr(result, "summary")
    assert {e["event"]["name"] for e in envelopes if e["event"]["type"] == "result"} == {"m", "undeclared"}
    assert sum(e["event"]["type"] == "log" and e["event"]["level"] == "warn" for e in envelopes) == 2
    assert envelopes[0]["event"]["result_definitions"] == []
    assert json.loads((store.dir / "run.json").read_text())["definitions"]["results"] == []


def test_duplicate_and_undeclared_results_warn_without_folding_events(tmp_path):
    script = '''#!/bin/sh
adb-emit result --name m --value 1
adb-emit result --name m --value 2
adb-emit result --name llm_calls --value 99
adb-emit llm-call --model mock/model <<'EOF'
{"input":[],"output":{}}
EOF
'''
    callbacks = []
    result, envelopes, _ = run_fixture(tmp_path, script=script, on_event=callbacks.append)
    assert [e["event"]["value"] for e in envelopes if e["event"]["type"] == "result"] == [1, 2, 99]
    assert sum(e["event"]["type"] == "llm.call" for e in envelopes) == 1
    assert callbacks == envelopes
    assert [e["seq"] for e in envelopes] == list(range(len(envelopes)))
    warnings = [e["event"]["message"] for e in envelopes if e["event"]["type"] == "log"]
    assert len(warnings) == 2
    assert "Repeated result 'm'" in warnings[0]
    assert "Undeclared result 'llm_calls'" in warnings[1]


def test_declarations_are_snapshotted_and_do_not_validate_values(tmp_path):
    from copy import deepcopy

    manifest = deepcopy(MANIFEST)
    expected = deepcopy(manifest["results"])

    def change_catalog(envelope):
        if envelope["event"].get("type") == "run.start":
            manifest["results"][0]["label"] = "Changed during run"
            manifest["results"][0]["details"] = "Changed calculation explanation"
            manifest["results"].append({"name": "added", "type": {"kind": "bool"}})

    script = "#!/bin/sh\nadb-emit result --name m --value unavailable\n"
    result, envelopes, store = run_fixture(
        tmp_path,
        script=script,
        manifest=manifest,
        on_event=change_catalog,
    )
    assert result.state == "completed"
    assert next(e["event"]["value"] for e in envelopes if e["event"]["type"] == "result") == "unavailable"
    assert envelopes[0]["event"]["result_definitions"] == expected
    assert (
        json.loads((store.dir / "run.json").read_text())["definitions"]["results"]
        == expected
    )


def test_provenance_is_saved_before_the_child_starts(tmp_path, monkeypatch):
    from adb_events import RunStart, read_events

    monkeypatch.setenv("ADB_RUNNER_BIN", "/nix/store/fixture-runner/bin/adb-runner")
    monkeypatch.setenv("ADB_NIX_SYSTEM", "x86_64-linux")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-recorded")
    starts = []

    def observe(envelope):
        event = envelope["event"]
        if event["type"] != "run.start":
            return
        record = json.loads((tmp_path / "home/runs/cid-t/20260916t120000z-012345abcdef/run.json").read_text())
        assert record["lifecycle"]["state"] == "provisioning"
        for field in (
            "seed",
            "runtime",
            "params",
        ):
            assert record["provenance" if field == "runtime" else "inputs"][field] == event[field]
        starts.append(event)

    _, _, store = run_fixture(tmp_path, script="#!/bin/sh\nexit 3\n", on_event=observe)
    assert len(starts) == 1
    start = next(read_events(store.dir)).event
    assert isinstance(start, RunStart)
    assert start.runtime.experiment_bin is None
    assert start.runtime.runner_bin == "/nix/store/fixture-runner/bin/adb-runner"
    assert start.runtime.runner_python_version
    assert start.runtime.cpu_model
    assert start.runtime.cpu_count == (os.process_cpu_count() or 1)
    assert start.seed == 42
    assert "must-not-be-recorded" not in (store.dir / "run.json").read_text()


def test_dev_launch_does_not_publish_home_paths(tmp_path, monkeypatch):
    directory = tmp_path / "home" / "developer"
    directory.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", "/home/developer/.venv/bin/python")
    monkeypatch.setenv("ADB_RUNNER_BIN", "/home/developer/.venv/bin/adb-runner")
    _, envelopes, store = run_fixture(directory, script="#!/bin/sh\n")
    start = envelopes[0]["event"]
    assert "/home" not in json.dumps(start)
    assert not {"experiment_bin", "runner_bin", "runner_python"} & start["runtime"].keys()
    assert "/home" not in (store.dir / "run.json").read_text()


@pytest.mark.parametrize("fetch_ref", [None, "dirty:legacy", "github:owner/repo/" + "a" * 40])
def test_tree_hash_is_independent_of_a_clean_fetch_reference(tmp_path, fetch_ref):
    _, envelopes, store = run_fixture(tmp_path, script="#!/bin/sh\n", fetch_ref=fetch_ref,
                                       tree_hash="sha256-packaging-tree")
    start = envelopes[0]["event"]
    metadata = json.loads((store.dir / "run.json").read_text())
    expected = fetch_ref if fetch_ref and not fetch_ref.startswith("dirty:") else None
    for saved in (start, metadata["provenance"]):
        assert saved.get("fetch_ref") == expected
        assert saved["tree_hash"] == "sha256-packaging-tree"
        assert "dirty" not in saved


@pytest.mark.parametrize("url,origin", [
    ("https://user:token@proxy.example:8443/v1/project?api_key=hidden#fragment", "https://proxy.example:8443"),
    ("http://user:token@[::1]:8000/v1?key=hidden", "http://[::1]:8000"),
    ("https://proxy.example/v1", "https://proxy.example"),
])
@pytest.mark.parametrize("provider,model,variable", [
    ("openai", "openai/model", "OPENAI_BASE_URL"),
    ("local-proxy", "openai-api/local-proxy/model", "LOCAL_PROXY_BASE_URL"),
])
def test_runtime_records_only_endpoint_origins(tmp_path, monkeypatch, url, origin, provider, model, variable):
    from adb_runner import protocol

    manifest = {"v": VOCABULARY_VERSION, "name": "t", "params": {"model": {"type": {"kind": "llm"}}}}
    # Observe the real spawn: sanitizing capture must not change the child's URL.
    spawn = protocol.subprocess.Popen

    def observe(*args, **kwargs):
        assert kwargs["env"][variable] == url
        return spawn(*args, **kwargs)

    monkeypatch.setattr(protocol.subprocess, "Popen", observe)
    _, envelopes, store = run_fixture(tmp_path, script="#!/bin/sh\n", params={"model": model},
                                       manifest=manifest, credential_env={variable: url})
    start = envelopes[0]["event"]
    assert start["runtime"]["endpoints"] == {provider: origin}
    assert all(secret not in json.dumps(start) for secret in ("user:", "token", "hidden", "/v1", "fragment"))
    assert json.loads((store.dir / "run.json").read_text())["provenance"]["runtime"] == start["runtime"]


def test_nonzero_exit_is_failed(tmp_path):
    result, envelopes, store = run_fixture(tmp_path, script="#!/bin/sh\nexit 3\n")
    assert result.state == "failed"
    assert envelopes[-1]["event"]["exit_code"] == 3
    assert envelopes[-1]["event"]["state"] == "failed"
    assert json.loads((store.dir / "run.json").read_text())["lifecycle"]["state"] == "failed"
    assert not any(e["event"]["type"] == "result" for e in envelopes)


def test_experiment_receives_params_and_env(tmp_path):
    # plain-text echo: lands in the stream as a captured `stdout` event, verbatim —
    # which is also the capture path a no-adapter wrapped tool exercises
    script = r"""#!/bin/sh
read -r p
echo "params=$p seed=$ADB_SEED run=$ADB_RUN_ID"
"""
    _, envelopes, _ = run_fixture(tmp_path, script=script, params={"x": 9})
    payloads = [e["event"] for e in envelopes]
    line = next(
        p["line"]
        for p in payloads
        if p.get("type") == "stdout" and "params=" in p["line"]
    )
    assert (
        '"x": 9' in line
        and "seed=42" in line
        and "run=20260916t120000z-012345abcdef" in line
    )


@pytest.mark.parametrize("stream, fd", [("stdout", 1), ("stderr", 2)])
def test_non_utf8_stdio_does_not_drop_subsequent_output(tmp_path, stream, fd):
    # Arbitrary child-process output uses a different path from the typed event
    # socket. Invalid bytes must not kill its reader or lose later valid text.
    script = (
        "#!/bin/sh\n"
        "read -r params\n"
        f"printf 'invalid byte: \\377\\n' >&{fd}\n"
        f"printf 'after invalid bytes\\n' >&{fd}\n"
    )
    result, envelopes, store = run_fixture(tmp_path, script=script)
    payloads = [envelope["event"] for envelope in envelopes]

    assert [event["line"] for event in payloads if event["type"] == stream] == [
        "invalid byte: \ufffd",
        "after invalid bytes",
    ]
    assert not any(
        event["type"] == "log" and "descendants still hold" in event["message"]
        for event in payloads
    )
    assert result.state == "completed"
    assert payloads[-1]["type"] == "run.end"
    assert json.loads((store.dir / "run.json").read_text())["lifecycle"]["state"] == "completed"


@pytest.mark.parametrize("stream, fd", [("stdout", 1), ("stderr", 2)])
def test_output_reader_failure_is_reported(tmp_path, monkeypatch, stream, fd):
    from adb_runner import protocol

    def fail_capture(**kwargs):
        raise RuntimeError("capture failure fixture")

    monkeypatch.setattr(protocol, "CapturedLine", fail_capture)
    result, envelopes, store = run_fixture(
        tmp_path, script=f"#!/bin/sh\nread -r params\necho text >&{fd}\n"
    )
    logs = [e["event"] for e in envelopes if e["event"]["type"] == "log"]
    assert logs == [
        {
            "type": "log",
            "level": "error",
            "message": f"failed to capture {stream}: capture failure fixture",
        }
    ]
    assert result.state == "failed"
    assert envelopes[-1]["event"]["state"] == "failed"
    assert json.loads((store.dir / "run.json").read_text())["lifecycle"]["state"] == "failed"


def test_orphaned_pipe_holders_do_not_hang_the_run(tmp_path):
    # a grandchild inheriting our pipes outlives the experiment; the reader loop
    # must drain and close within its grace period instead of waiting for EOF
    import time as _time

    script = (
        '#!/bin/sh\nsleep 30 &\necho \'{"type":"status","detail":"bye"}\'\nexit 0\n'
    )
    start = _time.monotonic()
    result, envelopes, _ = run_fixture(tmp_path, script=script)
    assert _time.monotonic() - start < 25  # not held hostage by the sleeping orphan
    assert result.state == "completed"
    assert any(
        "descendants still hold" in str(e["event"].get("message", ""))
        for e in envelopes
    )


def test_child_env_is_constructed_not_inherited(monkeypatch):
    # the store is the ONLY way provider credentials reach a run: an ambient key or
    # base-url exported in the shell neither leaks in nor shadows the stored value
    from adb_runner.protocol import child_env

    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ambient/v1")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-ambient")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    env = child_env("rid", "/run/dir", 7, {"OPENAI_API_KEY": "sk-stored"})
    assert env["OPENAI_API_KEY"] == "sk-stored"  # store wins over host
    assert "OPENAI_BASE_URL" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["DOCKER_HOST"] == "unix:///run/user/1000/docker.sock"  # allowlisted
    assert env["ADB_RUN_ID"] == "rid" and env["ADB_SEED"] == "7"
    stored = {"AWS_ACCESS_KEY_ID": "stored-access", "AWS_SECRET_ACCESS_KEY": "stored-secret"}
    env = child_env("rid", "/run/dir", 7, stored)
    assert {key: env[key] for key in stored} == stored


@pytest.mark.parametrize("locale", ["C", "fr_FR.UTF-8"])
def test_child_locale_is_pinned_regardless_of_parent(tmp_path, monkeypatch, locale):
    monkeypatch.setenv("LANG", locale)
    monkeypatch.setenv("LC_ALL", locale)
    result, records, _ = run_fixture(tmp_path, script='#!/bin/sh\nprintf "%s %s\\n" "$LANG" "$LC_ALL"\n',
                                    credential_env={"LANG": "other", "LC_ALL": "other"})
    assert result.state == "completed"
    assert {r["event"]["line"] for r in records if r["event"]["type"] == "stdout"} == {"C.UTF-8 C.UTF-8"}


def test_saved_run_deserializes_with_public_models(tmp_path):
    from adb_events import CustomEvent, Result, RunEnd, RunStart, read_events

    script = """#!/bin/sh
adb-emit custom --kind t.observation --data '{"resource":42}'
adb-emit result --name m --value 1
"""
    result, envelopes, store = run_fixture(tmp_path, script=script)
    records = list(read_events(store.dir))
    assert result.state == "completed"
    assert len(records) == len(envelopes)
    assert isinstance(records[0].event, RunStart)
    assert isinstance(records[1].event, CustomEvent)
    assert records[1].event.data == {"resource": 42}
    assert isinstance(records[2].event, Result)
    assert isinstance(records[-1].event, RunEnd)


def test_invalid_metrics_and_usage_do_not_break_run_or_poison_totals(tmp_path):
    from adb_events import read_events

    script = """#!/bin/sh
adb-emit result --name m --value 42
echo '{"type":"result","name":"m","value":{"bad":1}}'
echo '{"type":"llm.call","agent":"a","model":"x","usage":{"input_tokens":"bad"}}'
"""
    result, envelopes, store = run_fixture(tmp_path, script=script)
    assert result.state == "completed"
    assert next(e["event"]["value"] for e in envelopes if e["event"]["type"] == "result") == 42
    assert "usage_totals" not in envelopes[-1]["event"]


def test_concurrent_processes_record_large_events_and_cleanup_socket(tmp_path):
    from pathlib import Path
    from adb_events import CustomEvent, read_events

    child_code = "from adb_events import CustomEvent,emit; import sys; i=int(sys.argv[1]); emit(CustomEvent(kind='parallel',data={'id':i,'text':str(i)*200000}))"
    script = f"""#!{sys.executable}
import os, subprocess, sys
print(os.environ["ADB_EVENT_SOCKET"], flush=True)
children = [subprocess.Popen([sys.executable, "-c", {child_code!r}, str(i)]) for i in range(8)]
assert all(child.wait() == 0 for child in children)
"""
    result, envelopes, store = run_fixture(tmp_path, script=script)
    assert result.state == "completed"
    records = list(read_events(store.dir))
    events = [r.event for r in records if isinstance(r.event, CustomEvent)]
    assert sorted(e.data["id"] for e in events) == list(range(8))
    assert all(e.data["text"] == str(e.data["id"]) * 200000 for e in events)
    assert [r.seq for r in records] == list(range(len(records)))
    socket_path = next(
        e["event"]["line"] for e in envelopes if e["event"]["type"] == "stdout"
    )
    assert not Path(socket_path).exists()


def test_credential_env_reaches_child_without_entering_store(tmp_path):
    from adb_events.secrets import assert_run_has_no_secrets
    from adb_providers import PROVIDERS

    credentials = {
        provider.api_key.name: f"sk-{name}-" + "a" * 40
        for name, provider in PROVIDERS.items()
    } | {"ADB_TEST_SECRET": "fixture-secret-" + "b" * 40}
    # The child proves receipt without echoing the values into stdout or events.
    script = f"""#!{sys.executable}
import os
from adb_events import Status, emit
assert all(os.environ.get(key) == value for key, value in {credentials!r}.items())
emit(Status(detail="credentials received"))
"""
    result, envelopes, store = run_fixture(tmp_path, script=script, credential_env=credentials)
    assert result.state == "completed"
    assert {"type": "status", "detail": "credentials received"} in [e["event"] for e in envelopes]
    assert_run_has_no_secrets(store.dir, environment=credentials)
