"""One invocation creates one run with the seed passed to the experiment."""

import pytest

from reps_events import VOCABULARY_VERSION


@pytest.mark.parametrize("version", [None, 0, VOCABULARY_VERSION + 1])
def test_vocabulary_mismatch_refuses_before_any_run_files(tmp_path, monkeypatch, capsys, version):
    import json
    import sys
    from reps_runner import cli

    declaration = {"name": "fixture", "params": {}}
    if version is not None:
        declaration["v"] = version
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(declaration))
    marker = tmp_path / "child-started"
    experiment = tmp_path / "experiment"
    experiment.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.setattr(sys, "argv", ["reps-runner", "--data-dir", str(tmp_path / "runs")])
    assert cli.main() == 2
    assert not marker.exists() and not (tmp_path / "runs").exists()
    error = capsys.readouterr().err
    assert f"runner v={VOCABULARY_VERSION}" in error
    assert f"manifest v={version or 0}" in error



@pytest.mark.parametrize("ref", [
    "https://user:token@example.org/repo/revision",
    "git+https://user:token@example.org/repo?rev=abc",
    "ssh://user:token@example.org/repo?rev=abc",
    "git@example.org:repo",
])
def test_launch_rejects_fetch_ref_userinfo_before_writing_a_run(tmp_path, monkeypatch, capsys, ref):
    import json
    import sys
    from reps_runner import cli

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"name": "fixture", "params": {}, "v": VOCABULARY_VERSION}))
    marker = tmp_path / "child-started"
    experiment = tmp_path / "experiment"
    experiment.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.setenv("REPS_FETCH_REF", ref)
    monkeypatch.setattr(sys, "argv", ["reps-runner", "--data-dir", str(tmp_path / "runs")])
    assert cli.main() == 2
    assert not marker.exists()
    assert not (tmp_path / "runs").exists()
    captured = capsys.readouterr()
    assert "userinfo" in captured.err
    assert "token" not in captured.err and ref not in captured.err


@pytest.mark.parametrize("ref", ["", "github:owner/repo/" + "a" * 40])
def test_launch_forwards_optional_revision_and_tree_hash(tmp_path, monkeypatch, ref):
    import json
    import sys
    from reps_runner import cli

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"name": "fixture", "params": {}, "v": VOCABULARY_VERSION}))
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.setenv("REPS_FETCH_REF", ref)
    monkeypatch.setenv("REPS_TREE_HASH", "sha256-launcher-tree")
    home = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", ["reps-runner", "--data-dir", str(home)])
    assert cli.main() == 0
    [path] = home.glob("runs/*/*/run.json")
    metadata = json.loads(path.read_text())
    [events] = path.parent.glob("events.jsonl")
    start = json.loads(events.read_text().splitlines()[0])["event"]
    from reps_runner.canonical import condition_id
    from reps_runner.store import condition_name, find_run
    from reps_events import read_events
    cid = condition_id(metadata["identity"]["experiment"], metadata["provenance"]["source"], metadata["inputs"]["params"])
    assert len(cid) == 40 and cid == metadata["identity"]["condition"] == start["condition"]
    name = condition_name(cid, metadata["identity"]["experiment"])
    assert path.parent.parent.name == name
    assert find_run(home, metadata["identity"]["run"]) == path.parent
    assert all(record.run == path.parent.name == metadata["identity"]["run"] for record in read_events(path.parent))
    assert not (home / "conditions").exists()
    assert start["params"] == metadata["inputs"]["params"]
    assert start["source"] == metadata["provenance"]["source"]
    for saved in (start, metadata["provenance"]):
        assert saved["tree_hash"] == "sha256-launcher-tree"
        assert saved.get("fetch_ref") == (ref or None)


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    import json
    import sys
    from reps_runner import cli

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"name": "fixture", "params": {}, "v": VOCABULARY_VERSION}))
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\nprintf 'seed=%s\\n' \"$REPS_SEED\"\n")
    experiment.chmod(0o755)
    home = tmp_path / "data"
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.delenv("REPS_FETCH_REF", raising=False)

    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["reps-runner", "--json", "--data-dir", str(home), *args])
        return cli.main()

    return home, invoke


def assert_recorded_seed(path, seed):
    import json

    card = json.loads(path.read_text())
    events = [json.loads(line)["event"] for line in (path.parent / "events.jsonl").read_text().splitlines()]
    assert card["inputs"] == {"params": {}, "seed": seed}
    assert events[0]["type"] == "run.start" and events[0]["seed"] == seed
    assert events[-1]["type"] == "run.end" and events[-1]["state"] == "completed"
    assert sum(event["type"] == "run.start" for event in events) == 1
    assert [event["line"] for event in events if event["type"] == "stdout"] == [f"seed={seed}"]
    assert not {"replicate", "replicates", "base_seed"} & events[0].keys()


@pytest.mark.parametrize("seed", [0, 42, 2**31 - 1])
def test_explicit_seed_is_recorded_and_forwarded_unchanged(run_cli, monkeypatch, seed):
    from reps_runner import cli

    def unexpected_random(*args):
        raise AssertionError("an explicit seed must not draw a random seed")

    monkeypatch.setattr(cli.random.SystemRandom, "getrandbits", unexpected_random)
    home, invoke = run_cli
    assert invoke("--seed", str(seed)) == 0
    [path] = home.glob("runs/*/*/run.json")
    assert_recorded_seed(path, seed)


@pytest.mark.parametrize("seed", [0, 2**31 - 1])
def test_omitted_seed_draws_31_bits_and_records_the_draw(run_cli, monkeypatch, seed):
    from reps_runner import cli

    draws = []
    def draw(self, bits):
        draws.append(bits)
        return seed

    monkeypatch.setattr(cli.random.SystemRandom, "getrandbits", draw)
    home, invoke = run_cli
    assert invoke() == 0
    assert draws == [31]
    [path] = home.glob("runs/*/*/run.json")
    assert_recorded_seed(path, seed)


def test_seed_does_not_depend_on_condition_or_invocation(run_cli, monkeypatch):
    home, invoke = run_cli
    for source in ("content:first", "content:first", "content:changed"):
        monkeypatch.setenv("REPS_SOURCE", source)
        assert invoke("--seed", "42") == 0
    paths = list(home.glob("runs/*/*/run.json"))
    assert len(paths) == 3
    assert len({path.parent.name for path in paths}) == 3
    assert len({path.parent.parent.name for path in paths}) == 2
    for path in paths:
        assert_recorded_seed(path, 42)


def test_dry_run_prints_the_run_seed_without_creating_a_run(run_cli, capsys):
    home, invoke = run_cli
    assert invoke("--dry-run", "--seed", "42") == 0
    output = capsys.readouterr().out
    assert "seed:       42" in output
    assert "replicate" not in output and "base seed" not in output
    assert not home.exists()


@pytest.mark.parametrize("seed", ["-1", str(2**31), "4054925867", str(2**53), "not-an-integer"])
def test_invalid_seed_is_rejected_before_writing_a_run(run_cli, capsys, seed):
    home, invoke = run_cli
    with pytest.raises(SystemExit) as exc:
        invoke("--seed", seed)
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "--seed" in error
    if seed != "not-an-integer":
        assert "0..2147483647" in error
    else:
        assert "invalid seed value" in error
    assert not home.exists()


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("non_interactive", [False, True])
@pytest.mark.parametrize("terminal", [False, True])
def test_output_and_interaction_are_independent(
    tmp_path, monkeypatch, capsys, json_output, non_interactive, terminal
):
    import json
    import sys
    from reps_runner import cli

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"name": "fixture", "params": {}, "v": VOCABULARY_VERSION}))
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\nexit 0\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: terminal)
    interactions = []

    def resolve_credentials(*args, interactive, **kwargs):
        interactions.append(interactive)
        return {}

    monkeypatch.setattr(cli.credentials, "resolve_run_credentials", resolve_credentials)
    argv = ["reps-runner", "--data-dir", str(tmp_path / "runs")]
    if json_output:
        argv.append("--json")
    if non_interactive:
        argv.append("--non-interactive")
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 0
    assert interactions == [terminal and not non_interactive]
    output = capsys.readouterr().out
    if json_output:
        events = [json.loads(line)["event"] for line in output.splitlines()]
        assert events[0]["type"] == "run.start"
        assert events[-1]["type"] == "run.end"
    else:
        assert output == ""


@pytest.mark.parametrize("data_directory", ["flag", "env", "xdg", "default"], indirect=True)
def test_run_uses_selected_data_directory(data_directory, tmp_path, monkeypatch, capsys):
    import json
    import sys
    from reps_runner import cli

    home, flags = data_directory
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"name": "fixture", "params": {}, "v": VOCABULARY_VERSION}))
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\nexit 0\n")
    experiment.chmod(0o755)
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_EXPERIMENT_BIN", str(experiment))
    monkeypatch.delenv("REPS_FETCH_REF", raising=False)
    monkeypatch.setattr(sys, "argv", ["reps-runner", *flags])

    assert cli.main() == 0
    [card] = home.glob("runs/*/*/run.json")
    assert json.loads(card.read_text())["lifecycle"]["state"] == "completed"
    assert str(card.parent) in capsys.readouterr().err
    assert list(tmp_path.rglob("run.json")) == [card]


@pytest.mark.parametrize("option,value", [("--out", "unused"), ("--replicates", "2")])
def test_removed_options_are_rejected(tmp_path, monkeypatch, capsys, option, value):
    import sys
    from reps_runner import cli

    monkeypatch.setattr(sys, "argv", ["reps-runner", option, value])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert f"unrecognized arguments: {option}" in capsys.readouterr().err
    assert option not in cli.build_parser().format_help()
    assert not (tmp_path / "unused").exists()


def test_run_alias_is_rejected(monkeypatch, capsys):
    import sys
    from reps_runner import cli

    monkeypatch.setattr(sys, "argv", ["reps-runner", "run"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "unrecognized arguments: run" in capsys.readouterr().err


@pytest.mark.parametrize("command", [[], ["publish", "--to", "s3://throwaway/prefix"]])
def test_aws_profile_rejects_credential_syntax(monkeypatch, capsys, command):
    import sys
    from reps_runner import cli

    monkeypatch.setattr(sys, "argv", ["reps-runner", *command, "--profile", "openai=work"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "AWS --profile NAME cannot contain =" in capsys.readouterr().err


def test_credential_selections_reach_resolver(run_cli, monkeypatch):
    from reps_runner import cli

    _, invoke = run_cli
    seen = []

    def resolve_credentials(*args, selections, **kwargs):
        seen.append(selections)
        return {}

    monkeypatch.setattr(cli.credentials, "resolve_run_credentials", resolve_credentials)
    assert invoke("--credential", "openai=work", "--credential", "anthropic=research") == 0
    assert seen == [{"openai": "work", "anthropic": "research"}]
    assert "--credential SET=NAME" in cli.build_parser().format_help()
