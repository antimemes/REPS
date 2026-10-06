"""Check launcher declarations and execute clean/dirty flake provenance fixtures."""

from pathlib import Path
import json
import os
import shlex
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("nix-instantiate") is None, reason="requires Nix")
EXPRESSION = '''{ repo, origin, rev ? null, narHash ? null, resultJson ? "[]" }:
let
  root = builtins.toPath repo;
  base = (import (root + "/default.nix") {}).pkgs;
  # Inspect exactly the declaration passed to writeShellApplication. Its builder
  # only adds the shell wrapper; mkExperiment owns these exports and assertions.
  pkgs = base // {
    writeShellApplication = args: args.text;
    writeText = name: text: "/nix/store/fixture-manifest.json";
  };
  reps = import (root + "/pkgs/build-support") {
    inherit pkgs origin rev narHash;
    reps-runner = base.hello;
    pyproject-nix = null;
    uv2nix = null;
    pyproject-build-systems = null;
  };
in (reps.mkExperiment {
  name = "fixture"; summary = "fixture"; params = {};
  results = builtins.fromJSON resultJson;
  program = "/nix/store/fixture-program/bin/run";
  src = root + "/lib/reps-events/reps_events";
}).app
'''


def evaluate(tmp_path, origin, *, rev=None, tree_hash=None, results=None):
    expression = tmp_path / "launcher.nix"
    expression.write_text(EXPRESSION)
    command = ["nix-instantiate", "--eval", "--strict", "--json", str(expression),
               "--argstr", "repo", str(REPO), "--argstr", "origin", origin]
    if rev is not None:
        command += ["--argstr", "rev", rev]
    if tree_hash is not None:
        command += ["--argstr", "narHash", tree_hash]
    if results is not None:
        command += ["--argstr", "resultJson", json.dumps(results)]
    return subprocess.run(command, text=True, capture_output=True, timeout=60)


@pytest.mark.parametrize("rev", [None, "a" * 40])
@pytest.mark.parametrize("tree_hash", [None, "sha256-packaging-tree"])
def test_launcher_exports_tree_hash_independently_of_revision(tmp_path, rev, tree_hash):
    result = evaluate(tmp_path, "github:owner/repo?dir=packaging", rev=rev, tree_hash=tree_hash)
    assert result.returncode == 0, result.stderr
    exports = dict(shlex.split(line)[1].split("=", 1)
                   for line in json.loads(result.stdout).splitlines() if line.strip().startswith("export "))
    assert exports["REPS_TREE_HASH"] == (tree_hash or "")
    assert exports["REPS_FETCH_REF"] == (f"github:owner/repo/{rev}?dir=packaging" if rev else "")


@pytest.mark.parametrize("origin", [
    "https://user:token@example.org/repo", "git+https://user:token@example.org/repo",
    "ssh://git@example.org/repo", "git@example.org:repo",
    "https://user:token@example.org/repo\n",
])
def test_launcher_rejects_userinfo_before_exporting_fetch_ref(tmp_path, origin):
    result = evaluate(tmp_path, origin, rev="a" * 40)
    assert result.returncode != 0
    assert "fetch_ref must not contain userinfo" in result.stderr
    assert "user:token@" not in result.stderr


def test_build_fails_when_schema_pointer_does_not_import(tmp_path):
    expression = tmp_path / "bad-schema.nix"
    expression.write_text('''{ repo }:
      let
        root = builtins.toPath repo;
        packages = import (root + "/default.nix") {};
        reps = import (root + "/pkgs/build-support") {
          inherit (packages) pkgs reps-runner;
          origin = "github:test/repo";
          pyproject-nix = null; uv2nix = null; pyproject-build-systems = null;
        };
      in (reps.mkExperiment {
        name = "bad-schema-fixture"; summary = "fixture"; params = {};
        program = "/unused"; src = root + "/lib/reps-events/reps_events";
        schema = { version = 0; models = "reps_events:MissingPayload"; };
      }).app
    ''')
    result = subprocess.run(
        ["nix-build", "--no-out-link", str(expression), "--argstr", "repo", str(REPO)],
        text=True, capture_output=True, timeout=120,
    )
    assert result.returncode != 0
    assert "has no attribute 'MissingPayload'" in result.stderr, result.stderr


def test_clean_and_dirty_flake_launches_preserve_provenance(tmp_path):
    """Exercise real Git sourceInfo, launcher exports, and persisted process facts."""
    from reps_events import RunStart, read_events

    checkout = tmp_path / "fixture"
    checkout.mkdir()
    flake = r'''{
      outputs = { self }: let
        packages = import (builtins.toPath @REPO@) {};
        inherit (packages) pkgs;
        reps = import (builtins.toPath (@REPO@ + "/pkgs/build-support")) {
          inherit pkgs; inherit (packages) reps-runner;
          origin = "github:fixture/provenance";
          rev = self.rev or null;
          narHash = self.narHash;
          pyproject-nix = null; uv2nix = null; pyproject-build-systems = null;
        };
        experiment = reps.mkExperiment {
          name = "provenance-fixture"; summary = "fixture";
          params.model = { type = reps.types.llm; initial = "openai/fixture"; };
          src = ./program.py;
          program = pkgs.writeShellApplication {
            name = "provenance-child";
            text = "exec ${pkgs.python3}/bin/python ${./program.py}";
          };
        };
      in { apps.${pkgs.stdenv.hostPlatform.system}.default = {
        type = "app"; program = pkgs.lib.getExe experiment.app;
      }; };
    }'''
    (checkout / "flake.nix").write_text(flake.replace("@REPO@", json.dumps(str(REPO))))
    program = checkout / "program.py"
    program.write_text("""import json, os, sys
print(json.dumps({"params": json.load(sys.stdin), "seed": int(os.environ["REPS_SEED"]),
                  "endpoint": os.environ["OPENAI_BASE_URL"]}))
""")

    def command(*args, cwd=checkout, env=None):
        result = subprocess.run(args, cwd=cwd, env=env, text=True,
                                capture_output=True, timeout=300)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    # Commits are confined to this disposable test fixture, never the REPS checkout.
    command("git", "init", "-q")
    command("git", "add", "flake.nix", "program.py")
    command("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "clean provenance fixture")
    revision = command("git", "rev-parse", "HEAD")
    credential_file = tmp_path / "credentials.toml"
    endpoint = "https://api.example.invalid:8443/v1/models?route=test"
    credential_file.write_text('[openai.default]\nOPENAI_API_KEY="fixture-key"\n'
                               f'OPENAI_BASE_URL="{endpoint}"\n')
    credential_file.chmod(0o600)
    env = {**os.environ, "REPS_CREDENTIALS_FILE": str(credential_file),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}
    starts = []
    for dirty in (False, True):
        if dirty:
            program.write_text(program.read_text() + "# dirty packaging tree\n")
        info = json.loads(command("nix", "flake", "metadata", "--json", "--no-write-lock-file"))
        assert ("rev" in info["locked"]) == (not dirty)
        output = tmp_path / ("dirty-run" if dirty else "clean-run")
        command("nix", "run", "--impure", "--no-write-lock-file", ".", "--",
                "--data-dir", str(output), "--seed", "37",
                "--set", "model=openai/fixture", env=env)
        run_file, = output.glob("runs/*/*/run.json")
        records = list(read_events(run_file.parent))
        start = records[0].event
        assert isinstance(start, RunStart)
        assert start.seed == 37
        assert start.source.startswith("content:sha256:")
        assert start.fetch_ref == (None if dirty else f"github:fixture/provenance/{revision}")
        assert start.tree_hash == info["locked"]["narHash"]
        assert start.runtime.experiment_bin.startswith("/nix/store/")
        assert start.runtime.runner_bin.startswith("/nix/store/")
        assert start.runtime.endpoints == {"openai": "https://api.example.invalid:8443"}
        assert start.params == {"model": "openai/fixture"}
        child = json.loads(next(r.event.line for r in records if r.event.type == "stdout"))
        assert child == {"params": start.params, "seed": start.seed, "endpoint": endpoint}
        meta = json.loads(run_file.read_text())
        for key in ("source", "tree_hash", "params", "seed", "runtime"):
            assert meta["inputs" if key in ("params", "seed") else "provenance"][key] == start.model_dump(mode="json", exclude_none=True)[key]
        assert meta["provenance"].get("fetch_ref") == start.fetch_ref
        assert records[-1].event.state == "completed"
        starts.append(start)
    assert starts[0].source != starts[1].source
    assert starts[0].tree_hash != starts[1].tree_hash


def test_result_names_must_be_unique_at_nix_evaluation(tmp_path):
    declaration = {"name": "score", "type": {"kind": "int"}}
    unique = evaluate(tmp_path, "github:fixture/repo", results=[declaration])
    assert unique.returncode == 0, unique.stderr
    duplicate = evaluate(tmp_path, "github:fixture/repo", results=[declaration, declaration])
    assert duplicate.returncode != 0
    assert "duplicate result names" in duplicate.stderr


@pytest.mark.parametrize("results", [{"score": {"kind": "int"}}, [{"type": {"kind": "int"}}]])
def test_result_declarations_require_a_list_and_names(tmp_path, results):
    result = evaluate(tmp_path, "github:fixture/repo", results=results)
    assert result.returncode != 0
    assert "list of declarations with name and type" in result.stderr
