"""Manifest conformance sweep — every real nix-built manifest against the schema
vocabulary in adb_runner.schema.

Full structural validation, driven by the TypedDicts themselves (annotations are
introspected, so there is no second schema to drift): at every level, keys must
be declared, required keys present, and every VALUE must match its annotated
type — a manifest with `order: "1"` or a string where a list belongs fails here
with the file and path in the message. The walker checks the generated manifest
directly against the declared TypedDict vocabulary.

Gated on ADB_TEST_MANIFESTS (task test:python wires it to the nix-built manifests
dir); skips without it.
"""

import json
import os
import shutil
import subprocess
import types
import typing
from pathlib import Path

import pytest

from adb_events import Json
from adb_runner.schema import Manifest

# kinds the nix `types` constructors can produce (plus reserved run/harness kinds)
KINDS = {"llm", "str", "int", "float", "bool", "enum", "list", "struct", "object",
         "run", "harness"}


def _check(value: object, ann: object, path: str) -> None:
    """Assert `value` matches the annotation — TypedDicts (keys + required +
    recursive field types), list/dict generics, unions (first matching arm),
    scalars. `Json` means any JSON: it IS the deliberate don't-care."""
    if ann is Json:
        return
    if isinstance(ann, typing.TypeAliasType):  # `type X = ...` statements
        _check(value, ann.__value__, path)
        return
    origin = typing.get_origin(ann)
    if origin is typing.NotRequired:
        _check(value, typing.get_args(ann)[0], path)
        return
    if typing.is_typeddict(ann):
        assert isinstance(value, dict), f"{path}: expected object, got {type(value).__name__}"
        hints = typing.get_type_hints(ann)
        unknown = set(value) - set(hints)
        assert not unknown, f"{path}: keys {sorted(unknown)} not declared on {ann.__name__}"
        missing = ann.__required_keys__ - set(value)
        assert not missing, f"{path}: required keys {sorted(missing)} missing"
        for k, v in value.items():
            _check(v, hints[k], f"{path}.{k}")
        return
    if origin in (typing.Union, types.UnionType):
        errors: list[str] = []
        for arm in typing.get_args(ann):
            try:
                _check(value, arm, path)
                return
            except AssertionError as exc:
                errors.append(str(exc))
        raise AssertionError(f"{path}: {value!r} matches no arm of {ann}: {errors}")
    if origin is list:
        assert isinstance(value, list), f"{path}: expected list, got {type(value).__name__}"
        (item_ann,) = typing.get_args(ann)
        for i, item in enumerate(value):
            _check(item, item_ann, f"{path}[{i}]")
        return
    if origin is dict:
        assert isinstance(value, dict), f"{path}: expected object, got {type(value).__name__}"
        _key_ann, val_ann = typing.get_args(ann)
        for k, v in value.items():
            _check(v, val_ann, f"{path}.{k}")
        return
    if isinstance(ann, type):
        if ann is int:  # bool is an int subclass; keep them distinct
            assert isinstance(value, int) and not isinstance(value, bool), \
                f"{path}: expected int, got {type(value).__name__}"
        else:
            assert isinstance(value, ann), \
                f"{path}: expected {ann.__name__}, got {type(value).__name__}"
        return
    raise AssertionError(f"{path}: annotation {ann!r} not handled by this walker")


def _kinds(node: object) -> typing.Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "kind" and isinstance(v, str):
                yield v
            yield from _kinds(v)
    elif isinstance(node, list):
        for item in node:
            yield from _kinds(item)


def test_every_shipped_manifest_conforms():
    manifests_dir = os.environ.get("ADB_TEST_MANIFESTS")
    if not manifests_dir:
        pytest.skip("ADB_TEST_MANIFESTS unset (run via `task test:python`)")
    files = sorted(Path(manifests_dir).glob("*.json"))
    assert files, f"no manifests in {manifests_dir}"
    for f in files:
        doc = json.loads(f.read_text())
        _check(doc, Manifest, f.name)
        assert doc["schema_version"] == 1
        assert doc["schema"]["version"] >= 0
        assert ":" in doc["schema"]["models"]
        schema_path = Path(doc["schema"]["path"])
        assert schema_path.name == "schema.json"
        assert schema_path.parent == f.resolve().parent
        assert json.loads(schema_path.read_text())["$defs"]["LLMCall"]["x-adb-render"]["actor"] == "agent"
        assert (schema_path.parent / "shared-schema.json").is_file()
        unknown_kinds = set(_kinds([doc["params"], doc.get("results", [])])) - KINDS
        assert not unknown_kinds, f"{f.name}: unknown kinds {sorted(unknown_kinds)}"


def test_walker_rejects_wrong_types():
    """The walker itself must catch value-type drift, not just keys."""
    good = {"name": "x", "params": {"p": {"type": {"kind": "int"}, "order": 1}}}
    _check(good, Manifest, "good")
    _check({**good, "readme": "# Experiment\n\nDocumentation."}, Manifest, "readme")
    _check({**good, "results": [{"name": "score",
        "type": {"kind": "float"}, "label": "Score",
        "description": "Mean score over rounds.", "unit": "points",
        "details": "Each recorded round has equal weight.",
    }]}, Manifest, "results")
    for bad, why in [
        ({**good, "results": {"score": {"type": {"kind": "float"}}}}, "results must be a list"),
        ({**good, "results": [{"type": {"kind": "float"}}]}, "result name is required"),
        ({**good, "name": 1}, "name must be str"),
        ({**good, "readme": 1}, "readme must be str when present"),
        ({**good, "params": {"p": {"type": {"kind": "int"}, "order": "1"}}}, "order must be int"),
        ({**good, "params": {"p": {"type": {"kind": "enum", "values": "ab"}}}}, "values must be a list"),
        ({**good, "params": {"p": {"kind": "int"}}}, "decl missing required 'type'"),
        ({**good, "results": [{"name": "score", "kind": "float"}]}, "result must be wrapped"),
        ({**good, "results": [{"name": "score", "type": {"kind": "float"}, "label": 1}]}, "label must be str"),
        ({**good, "results": [{"name": "score", "type": {"kind": "float"}, "description": []}]}, "description must be str"),
        ({**good, "results": [{"name": "score", "type": {"kind": "float"}, "details": []}]}, "details must be str"),
        ({**good, "results": [{"name": "score", "type": {"kind": "float"}, "unit": False}]}, "unit must be str"),
    ]:
        with pytest.raises(AssertionError):
            _check(bad, Manifest, why)


def test_shipped_readmes_follow_presentation_format():
    manifests_dir = os.environ.get("ADB_TEST_MANIFESTS")
    if not manifests_dir:
        pytest.skip("ADB_TEST_MANIFESTS unset (run via `task test:python`)")
    root = Path(__file__).resolve().parents[2]
    catalog = Path(manifests_dir)
    govsim = json.loads((catalog / "govsim.json").read_text())
    assert [result["name"] for result in govsim["results"]] == [
        "rounds", "collapsed", "survival_months", "total_harvest", "gain_per_agent",
        "final_resource", "equality", "over_usage",
    ]
    assert not (root / "experiments/govsim/README.mdx").exists()
    assert govsim["readme"] == (root / "experiments/govsim/README.md").read_text()
    assert "](figures/" not in govsim["readme"]
    assert not any(path.is_file() for path in (catalog / "assets/govsim").rglob("*"))
    assert govsim["schema"]["version"] == 0
    assert govsim["schema"]["models"] == "govsim_adapter.models:Payload"
    # Resolve through the built experiment's interpreter, never by importing its
    # adapter into the runner's test environment.
    subprocess.run([
        str(Path(govsim["schema"]["path"]).parent / "python"), "-c",
        "import importlib, sys; from govsim_adapter.models import Payload; "
        "module, attr = sys.argv[1].split(':'); "
        "assert getattr(importlib.import_module(module), attr) is Payload",
        govsim["schema"]["models"],
    ], check=True)
    # An experiment directory without a README stays valid and omits the field.
    if not (root / "experiments/inspect_evals/README.md").exists():
        assert "readme" not in json.loads((catalog / "inspect-hello.json").read_text())


@pytest.mark.skipif(shutil.which("nix-build") is None, reason="requires Nix")
@pytest.mark.parametrize("formats", [("md",), ("mdx",), ("md", "mdx")])
def test_registry_readme_formats(tmp_path, formats):
    """Build the real registry against one disposable experiment directory."""
    root = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "checkout"
    shutil.copytree(root / "pkgs", fixture / "pkgs")
    for name in ("runner", "lib"):
        (fixture / name).symlink_to(root / name, target_is_directory=True)
    experiment = fixture / "experiments/readme-fixture"
    experiment.mkdir(parents=True)
    text = "# Fixture\n\nDocumentation from the experiment directory.\n"
    for suffix in formats:
        (experiment / f"README.{suffix}").write_text(text)
    (experiment / "overview.svg").write_text("<svg/>")
    (experiment / "package.nix").write_text('''{ adb }: {
      readme-fixture = adb.mkExperiment {
        name = "readme-fixture"; summary = "README registry fixture";
        params = {}; program = "/unused"; src = ./package.nix;
      };
    }''')
    expression = tmp_path / "manifests.nix"
    expression.write_text('''{ repo, fixture }:
      let
        root = builtins.toPath repo;
        sources = import (root + "/pkgs/locked-sources.nix") {};
        adb = import (builtins.toPath fixture + "/pkgs/top-level") {
          pkgs = (import (root + "/default.nix") {}).pkgs;
          inherit (sources) pyproject-nix uv2nix pyproject-build-systems;
        };
      in adb.manifests
    ''')
    result = subprocess.run([
        "nix-build", "--no-out-link", str(expression), "--argstr", "repo", str(root),
        "--argstr", "fixture", str(fixture),
    ], capture_output=True, text=True, timeout=180)
    if "mdx" in formats:
        assert result.returncode != 0
        assert "experiment readme-fixture: README.mdx is no longer supported; write README.md" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    catalog = Path(result.stdout.strip())
    manifest = json.loads((catalog / "readme-fixture.json").read_text())
    assert manifest["readme"] == text
    assert (catalog / "assets/readme-fixture/overview.svg").read_text() == "<svg/>"
