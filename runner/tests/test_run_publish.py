"""Completion publishing exercises the same real gate and temporary S3 service."""

import json
import sys

import boto3
import pytest

from reps_runner import cli
from test_publish import LICENSE, bucket, objects
from test_verify import saved


@pytest.fixture
def launch(saved, tmp_path, monkeypatch):
    _, manifest = saved
    # This fixture program only prints its environment; it declares no results.
    declaration = json.loads(manifest.read_text())
    declaration["results"] = []
    manifest.write_text(json.dumps(declaration))
    program = tmp_path / "experiment"
    program.write_text("#!/bin/sh\nenv\n")
    program.chmod(0o755)
    home = tmp_path / "run data's"
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(program))
    monkeypatch.setenv("REPS_FETCH_REF", "github:owner/repo/" + "a" * 40)
    def invoke(*extra, profile="throwaway", license="CC-BY-4.0"):
        monkeypatch.setattr(sys, "argv", ["reps-runner", "--data-dir", str(home),
            "--set", "x=1", "--publish", "s3://throwaway/completion",
            *(["--profile", profile] if profile is not None else []),
            *(["--license", license] if license is not None else []), *extra])
        return cli.main()
    return home, invoke, program


@pytest.mark.parametrize("profile", [None, "throwaway"])
def test_completion_verifies_uploads_only_runs_without_forwarding_aws(launch, bucket, monkeypatch, profile):
    home, invoke, _ = launch
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "unforwarded-test-secret")
    assert invoke(profile=profile) == 0
    assert invoke(profile=profile, license=None) == 0
    keys = objects(bucket)
    assert set(keys) == {"completion/license.json"} | {
        f"completion/{path.relative_to(home).as_posix()}/{filename}"
        for path in home.glob("runs/*/*") for filename in ["events.jsonl.zst", "run.json"]
    }
    assert len(keys) == 5
    assert keys["completion/license.json"] == LICENSE
    for path in home.glob("runs/*/*/events.jsonl"):
        assert "AWS_" not in path.read_text() and "unforwarded-test-secret" not in path.read_text()


def test_credential_and_aws_profile_selections_are_independent(launch, bucket, monkeypatch):
    _, invoke, _ = launch
    credentials, profiles = [], []
    def resolve_credentials(*args, selections, **kwargs):
        credentials.append(selections)
        return {}
    session = boto3.Session
    def aws_session(*args, **kwargs):
        profiles.append(kwargs["profile_name"])
        return session(*args, **kwargs)
    monkeypatch.setattr(cli.credentials, "resolve_run_credentials", resolve_credentials)
    monkeypatch.setattr("boto3.Session", aws_session)
    assert invoke("--credential", "openai=work", "--credential", "anthropic=research") == 0
    assert credentials == [{"openai": "work", "anthropic": "research"}]
    assert profiles == ["throwaway", "throwaway"]
    assert len(objects(bucket)) == 3


def test_failed_verification_skips_publishing_without_changing_exit(launch, bucket, monkeypatch, capsys):
    home, invoke, _ = launch
    execute = cli.execute_run
    def corrupt(**kwargs):
        result = execute(**kwargs)
        path = kwargs["store"].dir / "run.json"
        card = json.loads(path.read_text())
        card["inputs"]["seed"] += 1
        path.write_text(json.dumps(card))
        return result
    monkeypatch.setattr(cli, "execute_run", corrupt)
    assert invoke() == 0
    assert objects(bucket) == {"completion/license.json": LICENSE}
    assert "publish failed:" in capsys.readouterr().err
    [path] = home.glob("runs/*/*/run.json")
    assert json.loads(path.read_text())["lifecycle"]["state"] == "completed"


def test_unpinned_completion_stays_local_without_changing_exit(launch, bucket, monkeypatch, capsys):
    home, invoke, _ = launch
    monkeypatch.delenv("REPS_FETCH_REF")
    assert invoke() == 0
    assert objects(bucket) == {"completion/license.json": LICENSE}
    assert "publish failed: run has no provenance.fetch_ref" in capsys.readouterr().err
    [path] = home.glob("runs/*/*/run.json")
    card = json.loads(path.read_text())
    assert card["lifecycle"]["state"] == "completed"
    assert "fetch_ref" not in card["provenance"]


def test_publish_service_error_leaves_exit_zero_and_state_completed(launch, bucket, monkeypatch, capsys):
    home, invoke, _ = launch
    execute = cli.execute_run
    def remove_bucket(**kwargs):
        result = execute(**kwargs)
        bucket.delete_bucket(Bucket="throwaway")
        return result
    monkeypatch.setattr(cli, "execute_run", remove_bucket)
    assert invoke() == 0
    error = capsys.readouterr().err
    assert "publish failed:" in error and "NoSuchBucket" in error
    [path] = home.glob("runs/*/*/run.json")
    assert json.loads(path.read_text())["lifecycle"]["state"] == "completed"


def test_terminal_failed_run_can_publish_without_changing_exit(launch, bucket):
    _, invoke, program = launch
    program.write_text("#!/bin/sh\nexit 1\n")
    assert invoke() == 1
    [card] = [body for key, body in objects(bucket).items() if key.endswith("/run.json")]
    assert json.loads(card)["lifecycle"]["state"] == "failed"


@pytest.mark.parametrize("terminal, flags", [(False, []), (True, ["--non-interactive"])])
def test_undeclared_license_without_flag_fails_before_the_run(launch, bucket, monkeypatch, capsys, terminal, flags):
    home, invoke, _ = launch
    monkeypatch.setattr(sys.stdin, "isatty", lambda: terminal)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a non-interactive run never prompts"))
    assert invoke(*flags, license=None) == 2
    assert "reps: publish: target declares no license; pass --license SPDX" in capsys.readouterr().err
    assert not list(home.glob("runs/*/*"))
    assert objects(bucket) == {}


def test_mismatching_license_fails_before_the_run(launch, bucket, capsys):
    home, invoke, _ = launch
    declared = b'{"v":0,"spdx":"CC0-1.0"}\n'
    bucket.put_object(Bucket="throwaway", Key="completion/license.json", Body=declared)
    assert invoke() == 2
    assert ("reps: publish: s3://throwaway/completion/license.json declares CC0-1.0, not CC-BY-4.0; "
            "pass --license CC0-1.0 or choose a new prefix") in capsys.readouterr().err
    assert not list(home.glob("runs/*/*"))
    assert objects(bucket) == {"completion/license.json": declared}


def test_terminal_prompt_resolves_license_before_the_run(launch, bucket, monkeypatch, capsys):
    _, invoke, _ = launch
    order = []
    execute = cli.execute_run
    def run(**kwargs):
        order.append("run")
        return execute(**kwargs)
    def answer():
        order.append("prompt")
        return ""
    monkeypatch.setattr(cli, "execute_run", run)
    monkeypatch.setattr(cli.credentials, "resolve_run_credentials", lambda *args, **kwargs: {})
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answer)
    assert invoke(license=None) == 0
    assert order == ["prompt", "run"]
    error = capsys.readouterr().err
    assert "1) CC-BY-4.0 (default)" in error
    assert "reps: LICENSE s3://throwaway/completion CC-BY-4.0" in error
    assert objects(bucket)["completion/license.json"] == LICENSE


def test_license_declared_during_the_run_fails_publish_without_changing_state(launch, bucket, monkeypatch, capsys):
    home, invoke, _ = launch
    declared = b'{"v":0,"spdx":"CC0-1.0"}\n'
    execute = cli.execute_run
    def declare_meanwhile(**kwargs):
        result = execute(**kwargs)
        bucket.put_object(Bucket="throwaway", Key="completion/license.json", Body=declared)
        return result
    monkeypatch.setattr(cli, "execute_run", declare_meanwhile)
    assert invoke() == 0
    assert ("reps: publish failed: s3://throwaway/completion/license.json declares CC0-1.0, not CC-BY-4.0"
            in capsys.readouterr().err)
    assert objects(bucket) == {"completion/license.json": declared}
    [path] = home.glob("runs/*/*/run.json")
    assert json.loads(path.read_text())["lifecycle"]["state"] == "completed"


@pytest.mark.parametrize("declared", ["never", "before", "during"])
def test_no_license_run_publishes_without_writing_a_declaration(launch, bucket, monkeypatch, capsys, declared):
    home, invoke, _ = launch
    if declared == "before":
        bucket.put_object(Bucket="throwaway", Key="completion/license.json", Body=LICENSE)
    execute = cli.execute_run
    def run(**kwargs):
        result = execute(**kwargs)
        if declared == "during":
            bucket.put_object(Bucket="throwaway", Key="completion/license.json", Body=LICENSE)
        return result
    monkeypatch.setattr(cli, "execute_run", run)
    assert invoke("--non-interactive", license="NONE") == 0
    error = capsys.readouterr().err
    warning = "publish: WARNING: s3://throwaway/completion/license.json declares CC-BY-4.0; publishing under it"
    assert error.count(warning) == (0 if declared == "never" else 1)
    assert f"reps: LICENSE s3://throwaway/completion {'undeclared' if declared == 'never' else 'CC-BY-4.0'}" in error
    keys = objects(bucket)
    assert keys.pop("completion/license.json", None) == (None if declared == "never" else LICENSE)
    assert set(keys) == {
        f"completion/{path.relative_to(home).as_posix()}/{filename}"
        for path in home.glob("runs/*/*") for filename in ["events.jsonl.zst", "run.json"]
    }
    assert len(keys) == 2
