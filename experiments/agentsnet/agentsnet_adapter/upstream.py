"""Staging the authors' code and reading what it writes.

The authors ran `python main.py` from a checkout: `LiteralMessagePassing`,
`utils` and `main` importable from the repository root, graphs fetched from the
Hugging Face Hub, results written to `<root>/results/`. The tree comes from the
repository that package.nix pins by revision (upstream.json): the packaged
launcher names it with AGENTSNET_UPSTREAM (its revision with
AGENTSNET_UPSTREAM_REV) and the dataset file with AGENTSNET_DATASET; a developer
points the same variables at a checkout and a downloaded parquet. Each run
copies the tree into the run directory and works from there, so the layout the
authors ran from is reproduced and nothing in the store is written to. Nothing
in the copy is edited; the adapter only swaps module attributes at import time
(patch.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from reps_events import emit
from reps_experiment.scaffold import deposit_artifact

from .models import AgentsNetUpstreamRecord, UpstreamRecordData

ENV_TREE = "AGENTSNET_UPSTREAM"          # the authors' tree: the pinned store path, or a checkout
ENV_REV = "AGENTSNET_UPSTREAM_REV"       # its git revision, when the launcher knows it
ENV_DATASET = "AGENTSNET_DATASET"        # the parquet file of graph instances (dataset.json)
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


def stage(run_dir: Path) -> Path:
    """Copy the tree to `run_dir/upstream_work`, make it importable the way the
    authors ran it, and chdir into it (results/ is written relative to the cwd).
    Returns the work root."""
    work = run_dir / "upstream_work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(upstream_tree(), work, ignore=IGNORE)
    # the pinned tree is a read-only store path and copytree keeps its modes; the
    # authors' code writes results beside itself
    for directory, _subdirs, filenames in os.walk(work):
        os.chmod(directory, 0o755)
        for filename in filenames:
            os.chmod(os.path.join(directory, filename), 0o644)
    for name in UPSTREAM_MODULES:        # a fresh import against this copy
        sys.modules.pop(name, None)
    if str(work) not in sys.path:
        sys.path.insert(0, str(work))
    os.chdir(work)
    return work


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
