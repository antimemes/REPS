"""Probe actual experiment source hashes in an isolated copy of the checkout."""

from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import time
import tomllib

REPO = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "__pycache__", "node_modules", "dist", ".direnv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "result", "result-*",
)
DEV_HEADER = "[package.metadata.requires-dev]"


def replace_once(text, pattern, replacement):
    """A format change must fail a probe rather than silently make it a no-op."""
    changed, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    assert count == 1 and changed != text, f"Lock probe no longer matches: {pattern}"
    tomllib.loads(changed)
    return changed


def add_dev_entry(text, package):
    # uv sorts packages by name, so the experiment's own table is not necessarily first.
    blocks = re.split(r"(?m)(?=^\[\[package\]\]$)", text)
    for i, block in enumerate(blocks):
        if block.startswith("[[package]]") and tomllib.loads(block)["package"][0]["name"] == package:
            blocks[i] = replace_once(block, r"^\[package.metadata.requires-dev\]$",
                DEV_HEADER + '\nidentity_probe = [{ name = "pytest", specifier = ">=8.0" }]')
            return "".join(blocks)
    raise AssertionError(f"Lock probe package missing: {package}")


started = time.monotonic()
for lock in sorted((REPO / "experiments").glob("*/uv.lock")):
    assert DEV_HEADER in lock.read_text().splitlines(), (
        f"{lock.relative_to(REPO)}: missing {DEV_HEADER}; uv lock format drift: review the identity filter"
    )
    print(f"{lock.relative_to(REPO)}: requires-dev format tripwire passed", flush=True)


with tempfile.TemporaryDirectory(prefix="reps-identity-") as directory:
    root = Path(directory)
    checkout = root / "checkout"
    checkout.mkdir()
    for name in ("pkgs", "experiments", "lib", "runner"):
        shutil.copytree(REPO / name, checkout / name, ignore=IGNORE)
    for name in ("default.nix", "flake.lock", ".git-revision"):
        shutil.copyfile(REPO / name, checkout / name)
    expression = root / "identity.nix"
    expression.write_text('''{ repo }:
let
  root = builtins.toPath repo;
  pkgs = (import (root + "/default.nix") {}).pkgs;
  sources = import (root + "/pkgs/locked-sources.nix") {};
  reps = import (root + "/pkgs/top-level") {
    inherit pkgs;
    inherit (sources) pyproject-nix uv2nix pyproject-build-systems;
  };
in builtins.mapAttrs (_: exp: exp.source) {
  inherit (reps.experiments) govsim concordia inspect-hello impossiblebench-swebench;
}
''')

    def hashes():
        return json.loads(subprocess.check_output([
            "nix-instantiate", "--eval", "--strict", "--json", str(expression),
            "--argstr", "repo", str(checkout),
        ], text=True))

    def probe(relative, expected, *, label="source comment", transform=None):
        path = checkout / relative
        original = path.read_bytes()
        try:
            path.write_bytes(transform(original.decode()).encode() if transform else
                             original + b"\n# Source identity probe.\n")
            changed = {name for name, value in hashes().items() if value != before[name]}
            assert changed == expected, f"{relative} [{label}]: expected {expected}, got {changed}"
            print(f"{relative} [{label}]: changes {', '.join(sorted(changed)) or 'no experiment hashes'}", flush=True)
        finally:
            path.write_bytes(original)

    before = hashes()
    probe("experiments/govsim/govsim_adapter/main.py", {"govsim"})
    probe("lib/reps-events/reps_events/emit.py", set(before))
    probe("lib/reps-inspect/reps_inspect/translate.py", {"inspect-hello", "impossiblebench-swebench"})
    probe("lib/reps-events/tests/test_emit.py", set())
    probe("experiments/govsim/README.md", set())
    # Cover both explicit lock paths and locks inside a declared directory.
    for family, experiment in (("govsim", "govsim"), ("impossiblebench", "impossiblebench-swebench")):
        relative = f"experiments/{family}/uv.lock"
        project = tomllib.loads((checkout / "experiments" / family / "pyproject.toml").read_text())["project"]["name"]
        probe(relative, set(), label="dependency requires-dev entry",
              transform=lambda text: add_dev_entry(text, "reps-events"))
        probe(relative, set(), label=f"own requires-dev entry ({project})",
              transform=lambda text: add_dev_entry(text, project))
        for label, pattern, replacement in [
            ("resolved version", r'(^version = ")([^"]+)', lambda m: m[1] + m[2] + "+identity-probe"),
            ("wheel hash", r'(^wheels = \[\n[^\n]*hash = "sha256:)([0-9a-f])',
             lambda m: m[1] + ("0" if m[2] != "0" else "1")),
            ("sdist hash", r'(^sdist = \{[^\n]*hash = "sha256:)([0-9a-f])',
             lambda m: m[1] + ("0" if m[2] != "0" else "1")),
            ("dependencies entry", r'(^dependencies = \[\n\s+\{ name = ")([^"]+)',
             lambda m: m[1] + ("packaging" if m[2] != "packaging" else "six")),
            ("requires-dist specifier", r'specifier = ">=2\.12,<3"', 'specifier = ">=2.12.1,<3"'),
            ("optional-dependencies entry", r'(^\[package.optional-dependencies\]\n\w+ = \[\n\s+\{ name = ")([^"]+)',
             lambda m: m[1] + ("packaging" if m[2] != "packaging" else "six")),
            ("source registry", r'(^source = \{ registry = ")([^"]+)',
             lambda m: m[1] + m[2] + "/identity-probe"),
        ]:
            probe(relative, {experiment}, label=label,
                  transform=lambda text: replace_once(text, pattern, replacement))
        probe(relative, {experiment}, label="outside comment", transform=lambda text: "# Identity probe\n" + text)
        probe(relative, {experiment}, label="outside blank line", transform=lambda text: "\n" + text)
    cache = checkout / "lib/reps-events/reps_events/__pycache__"
    cache.mkdir()
    (cache / "ignored.pyc").write_bytes(b"development artifact")
    assert hashes() == before, "Development artifacts changed source identity"
    print(f"Shared source identity checks passed in {time.monotonic() - started:.2f}s")
