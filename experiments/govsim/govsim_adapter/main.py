"""govsim — GovSim ("Cooperate or Collapse", Piatti et al., NeurIPS 2024) as an
REPS experiment.

Five LLM personas share a common-pool resource (fishery / pasture / river):
each month they harvest concurrently, observe each other's catch, negotiate at
the restaurant, and reflect at home; the pool regrows doubling-capped, and the
commons collapses when it drops below 5. Upstream code runs from a pinned
package with a seed-forwarding patch; this program mirrors upstream
``simulation/main.py``'s construction site — config composition, model injection,
embedder substitution, wandb neutralization —
and extracts the paper's metrics from the persisted ``log_env.json``.
Persisted persona nodes are ingested too. ``persona_*/embeddings.json`` is
recomputable from node descriptions and the embedder named in ``govsim.config``
(``data.embedder``), so embeddings are left out of the event stream.

The program speaks the runner protocol (reps-experiment packages it): params
arrive as JSON on stdin (or a config path on argv for hand-runs), events leave
as JSON over the event socket. Any params or results change here must be mirrored in
package.nix.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Literal

from reps_events import Result, Status, emit
from reps_experiment.scaffold import experiment_main
from pydantic import BaseModel, Field, field_validator
from pydantic.warnings import UnsupportedFieldAttributeWarning
from .models import ConfigData, GovsimConfig
from .embedder import MXBAI_REVISION


EXPERIMENTS = (
    "fish_baseline_concurrent",
    "fish_baseline_concurrent_paraphrase_1",
    "fish_baseline_concurrent_paraphrase_2",
    "fish_baseline_concurrent_universalization",
    "fish_perturbation_no_language",
    "fish_perturbation_outsider",
    "fish_perturbation_outsider_universalization",
    "sheep_baseline_concurrent",
    "sheep_baseline_concurrent_universalization",
    "sheep_perturbation_no_language",
    "sheep_perturbation_outsider",
    "sheep_perturbation_outsider_universalization",
    "pollution_baseline_concurrent",
    "pollution_baseline_concurrent_universalization",
    "pollution_perturbation_no_language",
    "pollution_perturbation_outsider",
    "pollution_perturbation_outsider_universalization",
)


class Params(BaseModel):
    experiment: str    # upstream experiment config id (the condition)
    max_rounds: int = Field(ge=0)  # 0 = upstream per-experiment value
    model: str         # provider/model; mock/model = keyless offline
    embedder: Literal["hash", "mxbai"]      # hash | mxbai
    threads: int = Field(default=2, ge=1, strict=True)
    temperature: float | None = Field(ge=0, allow_inf_nan=False)
    top_p: float | None = Field(gt=0, le=1, allow_inf_nan=False)
    max_tokens: int = Field(gt=0)
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    @field_validator("experiment")
    @classmethod
    def known_experiment(cls, value: str) -> str:
        if value not in EXPERIMENTS:
            raise ValueError("unknown GovSim experiment")
        return value


def _compose(params: Params, seed: int):
    """Hydra compose over the installed upstream conf packages. Package searchpath
    entries replace upstream's cwd-relative ones (no chdir needed); compose
    strips the hydra node, so upstream's ``${uuid:}`` run-dir resolver is never
    consulted."""
    from hydra import compose, initialize_config_module

    searchpath = ",".join(
        f"pkg://simulation.scenarios.{s}.conf"
        for s in ("fishing", "sheep", "pollution")
    )
    overrides = [
        f"hydra.searchpath=[{searchpath}]",
        f"experiment={params.experiment}",
        "llm.is_api=true",
        f"+llm.reasoning_effort={json.dumps(params.reasoning_effort)}",
        f"llm.temperature={json.dumps(params.temperature)}",
        f"llm.top_p={json.dumps(params.top_p)}",
        f"seed={seed}",
        f"+experiment.env.seed={seed}",
        f"+embedder={params.embedder}",
        f"+embedder_revision={MXBAI_REVISION if params.embedder == 'mxbai' else 'null'}",
        f"+threads={params.threads}",
        "debug=true",
    ]
    if params.max_rounds > 0:
        overrides.append(f"experiment.env.max_num_rounds={params.max_rounds}")
    with initialize_config_module(config_module="simulation.conf", version_base=None):
        return compose("config", overrides=overrides)


def run(params: Params) -> None:
    # Set these before transformers, NumPy, PyTorch or upstream imports initialize
    # native thread pools. This is per-process and part of condition identity.
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = str(params.threads)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch

    torch.set_num_threads(params.threads)

    # Match the launcher's non-negative 31-bit range for provider seeds and
    # local RNGs; use this same value in cfg.seed, RNGs and ChatClient.
    seed = int(os.environ.get("REPS_SEED", "0")) & 0x7FFFFFFF
    # belt and braces on top of RepsLogger's debug=True: wandb must never leave
    # the run dir or touch the network — set before simulation.utils imports it
    os.environ.setdefault("WANDB_MODE", "disabled")
    os.environ.setdefault("WANDB_DIR", os.getcwd())

    cfg = _compose(params, seed)
    # The composed upstream model path is bypassed by our injected backend.
    # Save the effective model in the simulation config. The runner records
    # launch parameters, source identity and build provenance for every experiment.
    cfg.llm.path = params.model
    cfg.llm.backend = "reps-experiment.ChatClient"
    from omegaconf import OmegaConf
    from transformers import set_seed

    # Configuration belongs in the transcript, including on setup failure.
    emit(GovsimConfig(data=ConfigData.model_validate(OmegaConf.to_container(cfg, resolve=True))))

    set_seed(seed)  # mirrored from upstream main.py (global python/numpy/torch)

    # model: our pathfinder backend, wrapped in the upstream wrapper — never
    # pathfinder.get_model (its name-substring dispatch rejects REPS model ids).
    # Single-LLM only: upstream's mix_llm path is broken at these pins.
    from .backend import ChatClientBackend
    from .embedder import make_embedder
    from .mock import govsim_mock_responder

    backend = ChatClientBackend(
        params.model,
        seed,
        temperature=params.temperature,
        top_p=params.top_p,
        reasoning_effort=params.reasoning_effort,
        max_tokens=params.max_tokens,
        mock_responder=govsim_mock_responder,  # only consulted on mock/
    )

    from .logger import RepsLogger  # imports simulation.utils (wandb) — after env vars

    logger = RepsLogger(str(cfg.experiment.name), OmegaConf.to_object(cfg),
                       backend=backend)

    from simulation.utils import ModelWandbWrapper

    wrapper = ModelWandbWrapper(
        backend,
        render=False,
        wanbd_logger=logger,
        temperature=params.temperature,
        top_p=params.top_p,
        seed=seed,
        is_api=True,
    )
    wrappers = [wrapper] * cfg.experiment.personas.num
    embedding_model = make_embedder(params.embedder)

    # upstream writes results inside its package dir (read-only in the store);
    # ours land in the run's workspace (the cwd the runner gives us)
    storage = os.path.abspath(os.path.join("govsim_storage", params.experiment))
    os.makedirs(storage, exist_ok=True)

    from simulation.scenarios.fishing.run import run as run_fishing
    from simulation.scenarios.pollution.run import run as run_pollution
    from simulation.scenarios.sheep.run import run as run_sheep

    scenarios = {
        "fishing": run_fishing,
        "sheep": run_sheep,
        "pollution": run_pollution,
    }
    scenario = str(cfg.experiment.scenario)  # a declared field, never inferred
    if scenario not in scenarios:
        raise ValueError(f"unknown experiment.scenario: {scenario}")

    from .metrics import compute_metrics
    from .upstream import ingest_storage

    emit(Status(detail="Starting simulation"))
    log_env_path = Path(storage) / "log_env.json"
    rows = None
    try:
        scenarios[scenario](
            cfg.experiment, logger, wrappers, wrapper, embedding_model, storage,
        )
    finally:
        # Capture every completed native file even when the simulation fails.
        # Ingestion precedes Pandas so native values remain unchanged.
        simulation_error = sys.exception()
        try:
            rows = ingest_storage(Path(storage))
        except (ValueError, OSError) as exc:
            if simulation_error is None:
                raise
            # Diagnostics are already in the stream. A missing or broken
            # checkpoint must not replace the original simulation failure.
            simulation_error.add_note(f"Checkpoint ingestion also failed: {exc}")

    if rows is None:
        raise FileNotFoundError(log_env_path)
    import pandas as pd

    results = compute_metrics(pd.DataFrame(rows), int(cfg.experiment.env.max_num_rounds))

    for name, value in results.items():
        emit(Result(name=name, value=value))


def main() -> int:
    # wandb's generated GraphQL models attach Field(repr/frozen) to union
    # members; Pydantic warns at import time even with wandb disabled.
    warnings.filterwarnings("ignore", category=UnsupportedFieldAttributeWarning)
    # Hydra's defaults_list.py warns because upstream config omits _self_.
    warnings.filterwarnings(
        "ignore", message=r".*Defaults list is missing `_self_`.*",
        category=UserWarning, module=r"hydra\._internal\.defaults_list",
    )
    return experiment_main(Params, run, prog="govsim")
