"""Staging the authors' code and reading the files it writes.

The authors ran `python main.py` from a checkout: `LiteralMessagePassing`,
`utils` and `main` importable from the repository root, graphs fetched from the
Hugging Face Hub, results written to `<root>/results/`. The tree comes from the
repository that package.nix pins by revision (upstream.json): the packaged
launcher names it with AGENTSNET_UPSTREAM (its revision with
AGENTSNET_UPSTREAM_REV) and the dataset file with AGENTSNET_DATASET; a developer
points the same variables at a checkout and a downloaded parquet. Each run
copies the tree into the run directory and works from there. Thus the layout
the authors ran from is reproduced and no file in the Nix store is written to. The
copy is edited only by the fix patches a run switches on (AGENTSNET_PATCHES
names their directory, one `<param>.patch` for each switch); otherwise the adapter
only swaps module attributes at import time (monkeypatches.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from reps_events import emit
from reps_experiment.scaffold import deposit_artifact

from .models import AgentsNetUpstreamRecord, UpstreamRecordData

ENV_TREE = "AGENTSNET_UPSTREAM"          # the authors' tree: the pinned Nix store path, or a checkout
ENV_REV = "AGENTSNET_UPSTREAM_REV"       # its git revision, when the launcher knows it
ENV_DATASET = "AGENTSNET_DATASET"        # the parquet file of graph instances (dataset.json)
ENV_PATCHES = "AGENTSNET_PATCHES"        # the fix patches, one <param>.patch for each switch
ARTIFACT_KIND = "agentsnet.artifact"
UPSTREAM_MODULES = ("main", "LiteralMessagePassing", "utils", "generate_graphs", "chat_tool")
# left out of the copy: caches, anything a previous run wrote, the README image
IGNORE = shutil.ignore_patterns("__pycache__", ".DS_Store", ".git", "results", "graphs", "*.png")


def upstream_tree() -> Path:
    override = os.environ.get(ENV_TREE)
    if not override:
        raise RuntimeError(f"{ENV_TREE} is not set: it must name the authors' code tree (the repository pinned "
                           "in upstream.json; the packaged launcher sets it, a hand run points it at a checkout)")
    src = Path(override)
    if not (src / "LiteralMessagePassing.py").is_file():
        raise FileNotFoundError(f"{ENV_TREE}={override} has no LiteralMessagePassing.py")
    return src


def upstream_rev() -> str | None:
    return os.environ.get(ENV_REV) or None


def dataset_path() -> Path:
    override = os.environ.get(ENV_DATASET)
    if not override:
        raise RuntimeError(f"{ENV_DATASET} is not set: it must name the graph dataset parquet file (dataset.json; "
                           "the packaged launcher sets it)")
    path = Path(override)
    if not path.is_file():
        raise FileNotFoundError(f"{ENV_DATASET}={override} is not a file")
    return path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def stage(run_dir: Path, *, better_reprompting: bool, patch_crash_on_all_no: bool) -> Path:
    """Copy the tree to `run_dir/upstream_work`, apply the switched-on fix
    patches, make it importable the way the authors ran it, and chdir into it
    (results/ is written relative to the cwd). Returns the work root."""
    work = run_dir / "upstream_work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(upstream_tree(), work, ignore=IGNORE)
    # the pinned tree is a read-only Nix store path and copytree keeps its modes; the
    # authors' code writes results beside its source files
    for directory, _subdirs, filenames in os.walk(work):
        os.chmod(directory, 0o755)
        for filename in filenames:
            os.chmod(os.path.join(directory, filename), 0o644)
    if better_reprompting:               # name sequence, as tests/default.nix checks each combination
        _apply_patch(work, "better_reprompting")
    if patch_crash_on_all_no:
        _apply_patch(work, "patch_crash_on_all_no")
    for name in UPSTREAM_MODULES:        # a fresh import against this copy
        sys.modules.pop(name, None)
    if str(work) not in sys.path:
        sys.path.insert(0, str(work))
    os.chdir(work)
    return work


def _apply_patch(work: Path, name: str) -> None:
    """Same context or the run stops with an error; never a partially patched tree."""
    subprocess.run(["patch", "-d", str(work), "-p1", "--fuzz=0", "--batch", "--forward", "--quiet",
                    "-i", str(Path(os.environ[ENV_PATCHES]) / f"{name}.patch")], check=True)


def ingest_record(work: Path) -> dict:
    """The one results JSON upstream's save_results wrote: emit its marker,
    deposit the raw file as an artifact, return it parsed."""
    files = sorted((work / "results").glob("*.json"))
    if len(files) != 1:
        raise RuntimeError(f"expected one results file under {work / 'results'}, found {len(files)}")
    path = files[0]
    source = f"results/{path.name}"
    emit(AgentsNetUpstreamRecord(data=UpstreamRecordData(
        source=source, bytes=path.stat().st_size, sha256=sha256_of(path))))
    text = path.read_text(encoding="utf-8")
    deposit_artifact(source, text, filename="upstream_record.json", kind=ARTIFACT_KIND,
                     media_type="application/json")
    return json.loads(text)
