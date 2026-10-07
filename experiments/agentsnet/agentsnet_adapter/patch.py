"""Where the adapter touches the authors' code, at import time, without editing it.

Each item is a named replacement of an attribute on an upstream module. The
tree on disk stays verbatim.

  swap-chat-model    LiteralMessagePassing.init_chat_model and .ChatOllama ->
                     a factory returning RepsChatModel (llm.py). Upstream's
                     `.with_retry(stop_after_attempt=10)` applies to the
                     replacement as it did to the original (the ollama branch
                     has no retry wrapper upstream, and none here).
  no-throttle        upstream builds an InMemoryRateLimiter from a per-model-name
                     table (KeyError for a name not in it) to pace its own API
                     calls; pacing never changes a reply, so no limiter is used
                     here for any model: the table answers {} for every name and
                     .InMemoryRateLimiter returns None. REPS's client backs off
                     on 429s itself.
  env-ollama         upstream evaluates os.environ['OLLAMA_URI'] as the base_url
                     argument before the (replaced) ChatOllama is called; a
                     placeholder is set when absent, since routing is REPS's
                     (the ollama/ credential set), and the argument is ignored.
  dataset-pinned     main.load_dataset (Hugging Face Hub, network) -> a reader of
                     the parquet file package.nix pins by content; upstream's
                     own row selection in main.get_graph runs unchanged.
  seed-random        `random` and `numpy.random` seeded from REPS_SEED, as
                     upstream's run() seeds `random` from --seed. Nothing that
                     affects a reply draws from them (graph names come from the
                     dataset; only retry back-off timing uses `random`).
  seed-forwarded     every completion request carries the run seed (the REPS
                     client adds it); upstream sent none.
"""

from __future__ import annotations

import importlib
import os
import random
from typing import Any, Callable, Optional

import pandas as pd

from . import upstream
from .llm import RepsChatModel, make_chat_model


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else model


class _NoLimits(dict):
    """Stands in for upstream's per-model-name limiter table: every name gets {}."""

    def __missing__(self, key: str) -> dict:
        return {}


def apply_dataset() -> None:
    """dataset-pinned: before the first main.get_graph."""
    main = importlib.import_module("main")
    path = upstream.dataset_path()

    def load_dataset(*_args: Any, **_kwargs: Any) -> pd.DataFrame:
        return pd.read_parquet(path)       # main.get_graph wraps it in pd.DataFrame(...) itself
    main.load_dataset = load_dataset


def apply(model_id: str, seed: int, agent_names: dict[str, str],
          mock_responder: Optional[Callable[[list[dict[str, Any]]], str]] = None) -> None:
    lmp = importlib.import_module("LiteralMessagePassing")

    # env-ollama
    os.environ.setdefault("OLLAMA_URI", "unused-routed-through-reps")

    # seed-random
    random.seed(seed)
    try:
        import numpy

        numpy.random.seed(seed & 0xFFFFFFFF)
    except ImportError:
        pass

    # swap-chat-model: `init_chat_model(model_name, model_provider=..., rate_limiter=..., **chat_kwargs)`
    def init_chat_model(model: Optional[str] = None, *, model_provider: Optional[str] = None,
                        rate_limiter: Any = None, **kwargs: Any) -> RepsChatModel:
        return make_chat_model(model_id, run_seed=seed, agent_names=agent_names,
                               upstream_kwargs={"model": model, "model_provider": model_provider, **kwargs},
                               mock_responder=mock_responder)
    lmp.init_chat_model = init_chat_model

    # `ChatOllama(model=model_name, base_url=os.environ['OLLAMA_URI'])`
    def chat_ollama(model: Optional[str] = None, base_url: Optional[str] = None, **kwargs: Any) -> RepsChatModel:
        return make_chat_model(model_id, run_seed=seed, agent_names=agent_names,
                               upstream_kwargs={"model": model, "base_url": base_url, **kwargs},
                               mock_responder=mock_responder)
    lmp.ChatOllama = chat_ollama

    # no-throttle
    lmp.RATE_LIMITER_KWARGS = _NoLimits()
    lmp.InMemoryRateLimiter = lambda **_kwargs: None
