"""Check that Nix builds locked local sources and filters development artifacts."""

from pathlib import Path
import subprocess
import tempfile

REPO = Path(__file__).resolve().parents[1]


def output(*args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def package(root, name, dependencies="[]", sources=""):
    root.mkdir(parents=True)
    module = name.replace("-", "_")
    (root / module).mkdir()
    (root / module / "__init__.py").write_text('ORIGIN = "locked-fixture"\n')
    (root / "pyproject.toml").write_text(f"""
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = {dependencies}
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
{sources}
""")


with tempfile.TemporaryDirectory(prefix="reps-python-sources-") as directory:
    root = Path(directory)
    app = root / "app"
    # Deliberately use REPS names: the old inRepoOverlay replaced these sources.
    package(root / "libs/events", "reps-events")
    package(root / "libs/experiment", "reps-experiment")
    package(
        app,
        "source-fixture",
        '["reps-events", "reps-experiment"]',
        """
[tool.uv.sources]
reps-events = { path = "../libs/events", editable = true }
reps-experiment = { path = "../libs/experiment" }
""",
    )
    subprocess.run(["uv", "lock"], cwd=app, check=True)
    # Pass paths as arguments so checkout locations require no Nix escaping.
    expression = root / "build.nix"
    expression.write_text("""
{ repo, fixture }:
let
  pkgs = (import (builtins.toPath repo + "/default.nix") {}).pkgs;
  sources = import (builtins.toPath repo + "/pkgs/locked-sources.nix") {};
  pyproject-nix = import sources.pyproject-nix { inherit (pkgs) lib; };
  uv2nix = import sources.uv2nix { inherit (pkgs) lib; inherit pyproject-nix; };
  pyproject-build-systems = import sources.pyproject-build-systems {
    inherit (pkgs) lib;
    inherit pyproject-nix uv2nix;
  };
  reps = import (builtins.toPath repo + "/pkgs/build-support") {
    inherit pkgs pyproject-nix uv2nix pyproject-build-systems;
    origin = "fixture";
    reps-runner = null;
  };
in reps.mkPythonEnv {
  name = "source-fixture-env";
  workspaceRoot = /. + fixture;
  python = pkgs.python313;
}
""")
    arguments = [
        str(expression),
        "--argstr",
        "repo",
        str(REPO),
        "--argstr",
        "fixture",
        str(app),
    ]
    before = output("nix-instantiate", *arguments, cwd=root)
    declared = expression.read_text()
    expression.write_text(declared.replace("  python = pkgs.python313;\n", ""))
    missing = subprocess.run(["nix-instantiate", *arguments], cwd=root, text=True, capture_output=True)
    assert missing.returncode != 0
    assert "without required argument 'python'" in missing.stderr
    expression.write_text(declared)
    for source in [app, root / "libs/events", root / "libs/experiment"]:
        (source / ".venv").mkdir()
        (source / ".venv/irrelevant").write_text("must not enter build inputs")
    after = output("nix-instantiate", *arguments, cwd=root)
    assert before == after, "Development artifacts changed the build derivation"
    env = output("nix-build", "--no-out-link", *arguments, cwd=root)
    subprocess.run(
        [
            env + "/bin/python",
            "-c",
            """
import reps_events, reps_experiment
assert reps_events.ORIGIN == "locked-fixture"
assert reps_experiment.ORIGIN == "locked-fixture"
""",
        ],
        cwd=root,
        check=True,
    )
    (root / "libs/events/reps_events/__init__.py").write_text('ORIGIN = "changed"\n')
    changed = output("nix-instantiate", *arguments, cwd=root)
    assert changed != after, "Local implementation change did not reach the build"
    print("Python source selection and filtering: passed")
