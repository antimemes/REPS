"""The public command audits real saved runs without rewriting evidence."""

import hashlib
import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from adb_runner import cli, credentials
from adb_runner.verify import VerificationError, verify_run
from adb_events import LLMCall, ModelCall, ModelOutput, ChatCompletionChoice, ChatMessageAssistant, read_events
from adb_runner.card import CardProjection, derive_card
from test_protocol import MANIFEST, run_fixture


VENV = "/nix/store/" + "a" * 32 + "-experiment-env"
WRAPPER = "/nix/store/" + "b" * 32 + "-adapter/bin/adapter"
OTHER_VENV = "/nix/store/" + "c" * 32 + "-another-env"


def with_producer(directory, *, index=1, count=1, program=None, executable=None):
    def insert(rows):
        if program is not None:
            rows[0]["event"]["runtime"]["experiment_bin"] = program
        event = {"type": "producer.python", "implementation": "CPython", "version": "3.13.14",
                 "platform": "Linux", "flags": []}
        if executable is not None:
            event["executable"] = executable
        rows[index:index] = [{**rows[0], "event": event} for _ in range(count)]
        for seq, row in enumerate(rows):
            row["seq"] = seq
    rewrite_stream(directory, insert)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))


@pytest.fixture
def nix_wrapper(monkeypatch, tmp_path):
    """Model read-only Nix store files without creating or mutating store objects."""
    script = tmp_path / "adapter"
    script.write_text(f'#!/bin/sh\n{VENV}/bin/experiment "$@"\n')
    is_file, path_open = Path.is_file, Path.open
    monkeypatch.setattr(Path, "is_file", lambda path: str(path) == VENV + "/pyvenv.cfg" or is_file(path))
    monkeypatch.setattr(Path, "open", lambda path, *args, **kw:
                        path_open(script if str(path) == WRAPPER else path, *args, **kw))


@pytest.mark.parametrize("program", [VENV + "/bin/experiment", WRAPPER])
@pytest.mark.parametrize("matching", [True, False])
def test_producer_executable_must_belong_to_launch_venv(saved, nix_wrapper, program, matching):
    directory, manifest = saved
    with_producer(directory, program=program, executable=(VENV if matching else OTHER_VENV) + "/bin/python")
    before = digest_files(directory)
    if matching:
        assert verify_run(directory, manifest=manifest, environment={}).producer_warnings == ()
    else:
        with pytest.raises(VerificationError, match="outside experiment_bin's Python venv"):
            verify_run(directory, manifest=manifest, environment={})
    assert digest_files(directory) == before


def test_producer_unrecognized_launcher_notes_containment_skip(saved):
    directory, manifest = saved
    with_producer(directory, program=WRAPPER, executable=VENV + "/bin/python")
    result = verify_run(directory, manifest=manifest, environment={})
    assert len(result.producer_warnings) == 1
    assert "containment skipped" in result.producer_warnings[0]


@pytest.mark.parametrize("index,count,reason", [
    (1, 2, "exactly one"), (2, 1, "precede every other producer event"),
])
def test_producer_must_be_unique_and_first(saved, index, count, reason):
    directory, manifest = saved
    with_producer(directory, index=index, count=count)
    with pytest.raises(VerificationError, match=reason):
        verify_run(directory, manifest=manifest, environment={})


def test_captured_import_noise_can_precede_producer(saved):
    directory, manifest = saved
    rewrite_stream(directory, lambda rows: rows[1].update(event={"type": "stderr", "line": "import warning"}))
    with_producer(directory, index=2)
    assert verify_run(directory, manifest=manifest, environment={}).producer_warnings == ()


def test_legacy_producer_absence_warns_without_failing(saved, monkeypatch, capsys):
    directory, manifest = saved
    before = digest_files(directory)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 0
    output = capsys.readouterr()
    assert "WARN: producer.python absent (older producer)" in output.err
    assert "producer audit" in output.out and "verify: PASS:" in output.out
    assert digest_files(directory) == before


def _build_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    monkeypatch.delenv("ADB_MANIFEST", raising=False)
    monkeypatch.delenv("ADB_MANIFESTS", raising=False)
    models = tmp_path / "verify_models.py"
    models.write_text('''from typing import Annotated, Literal, Union
from pydantic import Field
from adb_events import CustomEvent, EVENT_MODELS
from adb_events.models.base import Model
class Data(Model):
    value: int
class Note(CustomEvent[Data]):
    kind: Literal["t.note"] = "t.note"
Payload = Annotated[Union[tuple({model for tag, model in EVENT_MODELS.items() if tag != "custom"}) + (Note,)], Field(discriminator="type")]
''')
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({**MANIFEST, "schema": {"version": 0, "models": "verify_models:Payload"}}))
    _, _, store = run_fixture(tmp_path, script='''#!/bin/sh
adb-emit custom --kind t.note --data '{"value":3}'
adb-emit result --name m --value 7
adb-emit result --name missing --value 1.0
''')
    return store.dir, manifest


@pytest.fixture
def saved(saved_template, tmp_path, monkeypatch):
    template, relative_run = saved_template
    shutil.copytree(template, tmp_path, dirs_exist_ok=True)
    monkeypatch.setenv("ADB_CREDENTIALS_FILE", str(tmp_path / "credentials.toml"))
    monkeypatch.delenv("ADB_MANIFEST", raising=False)
    monkeypatch.delenv("ADB_MANIFESTS", raising=False)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    return tmp_path / relative_run, tmp_path / "manifest.json"


def digest_files(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def use_legacy_build(manifest):
    """Give the union subprocess a test build with the original v0 envelope."""
    interpreter = manifest.parent / "legacy-bin" / "python"
    interpreter.parent.mkdir()
    interpreter.write_text(f'''#!{sys.executable}
import sys
import adb_events
from adb_events.models.base import NonNegativeInt
class LegacyEnvelope[T](adb_events.Envelope[T]):
    v: NonNegativeInt
adb_events.Envelope = LegacyEnvelope
script, sys.argv = sys.argv[2], ["-c", *sys.argv[3:]]
exec(script)
''')
    interpreter.chmod(0o755)
    declaration = json.loads(manifest.read_text())
    declaration["schema"]["path"] = str(interpreter.parent / "adb-emit")
    manifest.write_text(json.dumps(declaration))


def test_pre_hardware_stream_and_card_remain_verifiable(saved):
    """Tier-1 schema 0 records omit hardware; reading must not add wire fields."""
    directory, manifest = saved
    def old_runtime(rows):
        runtime = rows[0]["event"]["runtime"]
        runtime.pop("cpu_model")
        runtime.pop("cpu_count")
    rewrite_stream(directory, old_runtime)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    before = digest_files(directory)
    records = list(read_events(directory))
    assert records[0].event.runtime.cpu_model is None
    assert records[0].event.runtime.cpu_count is None
    assert [row.model_dump(mode="json", exclude_none=True) for row in records] == [
        json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    verify_run(directory, manifest=manifest)
    assert digest_files(directory) == before


@pytest.mark.parametrize("relative", [False, True])
def test_public_command_checks_all_three_and_preserves_every_file(saved, monkeypatch, capsys, relative):
    directory, manifest = saved
    before = digest_files(directory)
    monkeypatch.chdir(directory.parent)
    run_arg = "./" + directory.name if relative else str(directory)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", run_arg, "--manifest", str(manifest)])
    assert cli.main() == 0
    assert "verify: PASS:" in capsys.readouterr().out
    assert digest_files(directory) == before


@pytest.mark.parametrize("data_directory", ["flag", "env", "xdg", "default"], indirect=True)
def test_verify_resolves_run_id_in_selected_store(saved, data_directory, monkeypatch, capsys):
    directory, manifest = saved
    home, flags = data_directory
    original_home = directory.parents[2]
    relative_run = directory.relative_to(original_home)
    home.parent.mkdir(parents=True, exist_ok=True)
    original_home.rename(home)
    before = digest_files(home)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", directory.name, *flags,
                                     "--manifest", str(manifest)])
    assert cli.main() == 0
    assert "verify: PASS:" in capsys.readouterr().out
    assert (home / relative_run).is_dir()
    assert digest_files(home) == before


def test_verify_does_not_fall_back_to_another_store(saved, tmp_path, monkeypatch, capsys):
    directory, manifest = saved
    missing = tmp_path / "missing"
    monkeypatch.setenv("ADB_DATA_DIR", str(directory.parents[2]))
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", directory.name,
                                     "--data-dir", str(missing), "--manifest", str(manifest)])
    assert cli.main() == 1
    assert f"not found in {missing}" in capsys.readouterr().err
    assert not missing.exists()


@pytest.mark.parametrize("section", ["identity", "inputs", "lifecycle", "provenance", "definitions", "derived"])
def test_every_card_section_is_compared(saved, section):
    directory, manifest = saved
    path = directory / "run.json"
    card = json.loads(path.read_text())
    card[section]["invented"] = True
    path.write_text(json.dumps(card))
    with pytest.raises(VerificationError, match=rf"run.json.{section} differs"):
        verify_run(directory, manifest=manifest, environment={})


def rewrite_stream(directory, change):
    path = directory / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    change(records)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def test_missing_declared_result_fails_even_when_card_matches_stream(saved):
    directory, manifest = saved

    def omit_result(rows):
        rows[:] = [row for row in rows if not (
            row["event"]["type"] == "result" and row["event"]["name"] == "missing"
        )]
        for seq, row in enumerate(rows):
            row["seq"] = seq

    rewrite_stream(directory, omit_result)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    before = digest_files(directory)
    with pytest.raises(VerificationError, match=r"missing \['missing'\]; extra \[\]"):
        verify_run(directory, manifest=manifest, environment={})
    assert digest_files(directory) == before


def test_extra_reported_result_fails_against_manifest(saved):
    directory, manifest = saved
    declaration = json.loads(manifest.read_text())
    declaration["results"] = [result for result in declaration["results"] if result["name"] != "missing"]
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(VerificationError, match=r"missing \[\]; extra \['missing'\]"):
        verify_run(directory, manifest=manifest, environment={})


@pytest.mark.parametrize("mismatch", [False, True])
def test_all_served_models_checked_once_per_pair_and_truncated_calls_counted(saved, monkeypatch, capsys, mismatch):
    directory, manifest = saved
    choice = ChatCompletionChoice(message=ChatMessageAssistant(content="partial"), stop_reason="max_tokens")
    filtered = ChatCompletionChoice(message=ChatMessageAssistant(content=""), stop_reason="content_filter")
    calls = [LLMCall(model="azure/gpt-5-nano", input=[], output=ModelOutput(
        model="GPT-5-Nano-2025-08-07", choices=[filtered, filtered]))]
    for served in (["gpt-5-mini", "gpt-5-mini", "gpt-4.1"] if mismatch else ["gpt-5-nano"]):
        calls.append(LLMCall(model="azure/gpt-5-nano", input=[], output=ModelOutput(model=served, choices=[choice, choice])))

    def insert(rows):
        rows[-1:-1] = [{**rows[0], "event": call.model_dump(mode="json", exclude_none=True)} for call in calls]
        for seq, row in enumerate(rows):
            row["seq"] = seq
    rewrite_stream(directory, insert)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    result = verify_run(directory, manifest=manifest, environment={})
    assert result.max_tokens_stops == len(calls) - 1  # once per call, not per choice
    assert result.content_filter_stops == 1
    expected = (("azure/gpt-5-nano", "gpt-4.1"), ("azure/gpt-5-nano", "gpt-5-mini")) if mismatch else ()
    assert result.model_mismatches == expected
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == int(mismatch)
    output = capsys.readouterr()
    assert f"max_tokens stops: {len(calls) - 1}; content_filter stops: 1; empty_responses: 0 (llm.call records)" in output.out
    assert output.err.count("WARN: served model mismatch") == (2 if mismatch else 0)


@pytest.mark.parametrize("served", ["", "gpt-5-nano"])
@pytest.mark.parametrize("old_card", [True, False])
def test_legacy_empty_responses_fail_without_changing_records(saved, monkeypatch, capsys, served, old_card):
    directory, manifest = saved
    calls = [
        LLMCall(model="azure/gpt-5-nano", input=[], output=ModelOutput(model=served)),
        LLMCall(model="azure/gpt-5-nano", input=[], output=ModelOutput(
            model="gpt-5-nano", choices=[ChatCompletionChoice(
                message=ChatMessageAssistant(content=""), stop_reason="stop")])),
    ]

    def insert(rows):
        rows[-1:-1] = [{**rows[0], "event": call.model_dump(mode="json", exclude_none=True)} for call in calls]
        for seq, row in enumerate(rows):
            row.update(seq=seq, v=0)

    rewrite_stream(directory, insert)
    declaration = json.loads(manifest.read_text())
    declaration.pop("v")
    manifest.write_text(json.dumps(declaration))
    use_legacy_build(manifest)
    upgraded_card = derive_card(read_events(directory))
    assert upgraded_card["derived"]["counts"]["failed_calls"] == 1
    original = CardProjection()
    for line in (directory / "events.jsonl").read_text().splitlines():
        original.observe(json.loads(line))
    assert original.snapshot()["derived"]["counts"]["failed_calls"] == 0
    (directory / "run.json").write_text(json.dumps(original.snapshot() if old_card else upgraded_card))
    before = digest_files(directory)
    with pytest.raises(VerificationError, match="failed model calls: 1; first error: Empty choices"):
        verify_run(directory, manifest=manifest, environment={})
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 1
    assert "Empty choices for model 'azure/gpt-5-nano' (version 0 record, not retried)" in capsys.readouterr().err
    assert digest_files(directory) == before


@pytest.mark.parametrize("version,count,placeholder", [(0, 1, False), (1, 1, False), (1, 2, False), (1, 1, True)])
def test_failed_model_observations_fail_with_count_and_first_message(saved, monkeypatch, capsys, version, count, placeholder):
    directory, manifest = saved
    choices = [ChatCompletionChoice(message=ChatMessageAssistant(content=""))] if placeholder else []
    calls = [LLMCall(model="azure/model", input=[], output=ModelOutput(choices=choices),
                     call=ModelCall(request={}, response={"error": "rate limit"} if i == 0 else None,
                                    error=None if i == 0 else True),
                     error="HTTP 429: rate limit exceeded" if i == 0 else "Request timed out.")
             for i in range(count)]

    def insert(rows):
        rows[-1:-1] = [{**rows[0], "event": call.model_dump(mode="json", exclude_none=True)} for call in calls]
        for seq, row in enumerate(rows):
            row.update(seq=seq, v=version)

    rewrite_stream(directory, insert)
    declaration = json.loads(manifest.read_text())
    declaration["v"] = version
    manifest.write_text(json.dumps(declaration))
    if version == 0:
        use_legacy_build(manifest)
    recorded_calls = [record.event for record in read_events(directory) if record.event.type == "llm.call"]
    assert recorded_calls[0].call.error is None
    assert bool(recorded_calls[0].output.choices) is placeholder
    card = derive_card(read_events(directory))
    assert card["derived"]["counts"]["failed_calls"] == count
    (directory / "run.json").write_text(json.dumps(card))
    before = digest_files(directory)
    with pytest.raises(VerificationError, match=f"failed model calls: {count}; first error: HTTP 429"):
        verify_run(directory, manifest=manifest, environment={})
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 1
    assert f"failed model calls: {count}; first error: HTTP 429" in capsys.readouterr().err
    assert digest_files(directory) == before


@pytest.mark.parametrize("no_model", [False, True])
@pytest.mark.parametrize("old_card", [False, True])
def test_legacy_content_filters_pass_without_rewriting_evidence(saved, monkeypatch, capsys, no_model, old_card):
    directory, manifest = saved
    filename = "azure-content-filter-no-model-v0.json" if no_model else "azure-content-filter-v0.json"
    fixture = Path(__file__).resolve().parents[2] / "lib/adb-events/tests/fixtures" / filename
    event = json.loads(fixture.read_text())
    event["call"]["request"]["seed"] = 42

    def insert(rows):
        rows[-1:-1] = [{**rows[0], "event": event} for _ in range(2)]
        for seq, row in enumerate(rows):
            row.update(seq=seq, v=0)

    rewrite_stream(directory, insert)
    declaration = json.loads(manifest.read_text())
    declaration.pop("v")  # older builds did not write a vocabulary version
    manifest.write_text(json.dumps(declaration))
    use_legacy_build(manifest)
    upgraded_card = derive_card(read_events(directory))
    assert upgraded_card["derived"]["counts"]["failed_calls"] == 0
    # The historical union must validate the written error, not lifted output.
    with (manifest.parent / "verify_models.py").open("a") as models:
        models.write('''
from adb_events import LLMCall
class LegacyCall(LLMCall):
    error: str
Payload = Annotated[Union[tuple(model for tag, model in EVENT_MODELS.items()
    if tag not in {"custom", "llm.call"}) + (Note, LegacyCall)], Field(discriminator="type")]
''')
    projection = CardProjection()
    for line in (directory / "events.jsonl").read_text().splitlines():
        projection.observe(json.loads(line))
    assert projection.snapshot()["derived"]["counts"]["failed_calls"] == 2
    (directory / "run.json").write_text(json.dumps(projection.snapshot() if old_card else upgraded_card))
    before = digest_files(directory)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 0
    output = capsys.readouterr()
    assert "content_filter stops: 2" in output.out
    assert output.err.count("model identity could not be checked on content-filtered calls") == int(no_model)
    assert digest_files(directory) == before
    card = json.loads((directory / "run.json").read_text())
    if old_card:
        card["derived"]["counts"]["failed_calls"] = 2.0
        (directory / "run.json").write_text(json.dumps(card))
        with pytest.raises(VerificationError, match="differs from the stream projection"):
            verify_run(directory, manifest=manifest, environment={})
    card["derived"]["counts"]["failed_calls"] = 9
    (directory / "run.json").write_text(json.dumps(card))
    with pytest.raises(VerificationError, match="differs from the stream projection"):
        verify_run(directory, manifest=manifest, environment={})


@pytest.mark.parametrize("version", [0, None, 2])
def test_verify_does_not_compare_written_v_with_manifest(saved, version):
    directory, manifest = saved
    declaration = json.loads(manifest.read_text())
    if version is None:
        declaration.pop("v")
    else:
        declaration["v"] = version
    manifest.write_text(json.dumps(declaration))
    before = digest_files(directory)
    assert verify_run(directory, manifest=manifest, environment={}).records > 0
    assert digest_files(directory) == before


@pytest.mark.parametrize("version,stops,allowed,mismatch", [
    (1, ["content_filter"], True, False), (1, ["content_filter", "content_filter"], True, False),
    (1, ["content_filter", "stop"], False, True), (1, ["stop"], False, True),
    (0, ["content_filter"], True, False),
])
def test_unnamed_model_exception_requires_only_filtered_choices(saved, version, stops, allowed, mismatch):
    directory, manifest = saved
    event = LLMCall(model="azure/model", input=[], output=ModelOutput(model="", choices=[
        ChatCompletionChoice(message=ChatMessageAssistant(content=""), stop_reason=stop) for stop in stops
    ]))

    def insert(rows):
        rows.insert(-1, {**rows[0], "event": event.model_dump(mode="json", exclude_none=True)})
        for seq, row in enumerate(rows):
            row.update(seq=seq, v=version)

    rewrite_stream(directory, insert)
    declaration = json.loads(manifest.read_text())
    declaration["v"] = version
    manifest.write_text(json.dumps(declaration))
    if version == 0:
        use_legacy_build(manifest)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    result = verify_run(directory, manifest=manifest, environment={})
    assert result.filtered_identity_unavailable is allowed
    assert result.model_mismatches == ((("azure/model", ""),) if mismatch else ())


def test_custom_payload_is_checked_against_experiment_union(saved):
    directory, manifest = saved
    rewrite_stream(directory, lambda rows: rows[1]["event"]["data"].update(value="not-an-int"))
    with pytest.raises(VerificationError, match="experiment payload validation failed at events.jsonl:2"):
        verify_run(directory, manifest=manifest, environment={})


@pytest.mark.parametrize("change,reason", [
    (lambda rows: rows.pop(), "finish with run.end"),
    (lambda rows: rows[1].update(seq=99), "non-contiguous"),
    (lambda rows: rows[1].update(experiment="another"), "identity differs"),
    (lambda rows: rows[1].update(schema=9), "identity differs"),
    (lambda rows: rows[1].update(v=2), "vocabulary migration failed at events.jsonl:2"),
    (lambda rows: rows[1].pop("v"), "vocabulary migration failed at events.jsonl:2"),
    (lambda rows: rows[1].update(v="do-not-echo-this-value"), "vocabulary migration failed at events.jsonl:2"),
])
def test_incomplete_or_inconsistent_stream_fails(saved, change, reason):
    directory, manifest = saved
    rewrite_stream(directory, change)
    with pytest.raises(VerificationError, match=reason) as caught:
        verify_run(directory, manifest=manifest, environment={})
    assert "do-not-echo-this-value" not in str(caught.value)


def test_truncated_line_failure_never_echoes_input(saved, monkeypatch, capsys):
    directory, manifest = saved
    with (directory / "events.jsonl").open("a") as file:
        file.write('{"private": "do-not-echo-this-value"')
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 1
    output = capsys.readouterr()
    assert "envelope validation failed" in output.err
    assert "do-not-echo-this-value" not in output.err


@pytest.mark.parametrize("body", [
    "do-not-echo-this-value", "{'body': b'do-not-echo-this-value'}",
    "{'choices': 'do-not-echo-this-value'}",
])
def test_applicable_migration_failure_is_named_without_echoing_body(saved, monkeypatch, capsys, body):
    directory, manifest = saved
    event = LLMCall(model="azure/model", input=[], output=ModelOutput(),
                    error=f"Error code: 400 - {body}")
    rewrite_stream(directory, lambda rows: rows[1].update(v=0, event=event.model_dump(mode="json")))
    before = digest_files(directory)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 1
    output = capsys.readouterr()
    assert "vocabulary migration failed at events.jsonl:2" in output.err
    assert "do-not-echo-this-value" not in output.err
    assert digest_files(directory) == before


@pytest.mark.parametrize("origin", ["environment", "stored-profile"])
def test_scan_checks_real_credential_sources_and_workspace_without_echoing(saved, monkeypatch, capsys, origin):
    directory, manifest = saved
    secret = "opaque-provider-credential-for-audit"
    if origin == "environment":
        monkeypatch.setenv("TEST_ACCESS_TOKEN", secret)
    else:
        credentials.save({"provider": {"another-profile": {"CUSTOM_SECRET": secret,
                                                           "PROVIDER_BASE_URL": "https://api.example.invalid/v1"}}})
        endpoints = {"provider": "https://api.example.invalid"}
        rewrite_stream(directory, lambda rows: rows[0]["event"]["runtime"].update(endpoints=endpoints))
        card_path = directory / "run.json"
        card = json.loads(card_path.read_text())
        card["provenance"]["runtime"]["endpoints"] = endpoints
        card_path.write_text(json.dumps(card))
    workspace = directory / "workspace" / "nested"
    workspace.mkdir()
    (workspace / "config.json").write_text(json.dumps({"leaked": secret}))
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    assert cli.main() == 1
    output = capsys.readouterr()
    assert "secrets scan" in output.err
    assert "workspace/nested/config.json" in output.err
    assert secret not in output.out + output.err


def test_shell_key_settings_are_not_credentials(saved, monkeypatch):
    directory, manifest = saved
    monkeypatch.setenv("KEYTIMEOUT", "1")
    assert verify_run(directory, manifest=manifest).records > 0


def test_unused_endpoint_placeholder_is_not_a_run_credential(saved):
    directory, manifest = saved
    credentials.save({"openai": {"local": {"OPENAI_API_KEY": "mock",
                                         "OPENAI_BASE_URL": "http://localhost:11434/v1"}}})
    (directory / "workspace" / "notes.txt").write_text("A mock experiment")
    assert verify_run(directory, manifest=manifest, environment={}).credential_values == 0


def test_manifest_must_match_the_recorded_schema(saved):
    directory, manifest = saved
    declaration = json.loads(manifest.read_text())
    declaration["schema"]["version"] = 1
    manifest.write_text(json.dumps(declaration))
    with pytest.raises(VerificationError, match="experiment/schema does not match"):
        verify_run(directory, manifest=manifest, environment={})


def test_catalog_lookup_and_built_interpreter(saved, tmp_path):
    directory, manifest = saved
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    declaration = json.loads(manifest.read_text())
    declaration["schema"]["path"] = str(catalog / "schema.json")
    interpreter = catalog / "python"
    interpreter.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    interpreter.chmod(0o755)
    (catalog / "t.json").write_text(json.dumps(declaration))
    assert verify_run(directory, catalog=catalog, environment={}).records > 0
    (catalog / "python").unlink()
    with pytest.raises(VerificationError, match="interpreter is unavailable"):
        verify_run(directory, catalog=catalog, environment={})


@pytest.mark.parametrize("seed", ["matching", "absent", 999, None, True, "7"])
def test_every_request_seed_matches_the_recorded_run_seed(saved, monkeypatch, capsys, seed):
    directory, manifest = saved

    def insert(rows):
        run_seed = rows[0]["event"]["seed"]
        requests = [{"seed": run_seed}, {} if seed == "absent" else {
            "seed": run_seed if seed == "matching" else seed}]
        rows[-1:-1] = [{**rows[0], "event": {
            "type": "llm.call", "model": "mock/model", "input": [], "output": {"model": "model", "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "stop_reason": "stop"}]},
            "call": {"request": request, "response": {}},
        }} for request in requests]
        for seq, row in enumerate(rows):
            row["seq"] = seq

    rewrite_stream(directory, insert)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    before = digest_files(directory)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "verify", str(directory), "--manifest", str(manifest)])
    valid = seed in ("matching", "absent")
    assert cli.main() == (0 if valid else 1)
    output = capsys.readouterr()
    assert ("request seeds match" in output.out) if valid else ("call.request.seed differs" in output.err)
    assert digest_files(directory) == before
