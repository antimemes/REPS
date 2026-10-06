"""Typed params (pydantic) — the boundary where config becomes structure.

Everything the Inspect run needs arrives as explicit values: which task, which
model, task/model args, and the usual sample/generation limits. This program is
repo-unaware — it maps these onto `inspect_ai.eval(...)` and translates the
resulting EvalLog into ADB events (docs/book/src/reference/events.md).
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from adb_providers import PROVIDERS

# ADB canonical provider prefix -> (inspect's name for the same backend, env-var
# bridge from ADB's canonical vars to the vars inspect's provider reads). The
# `types.llm` vocabulary speaks provider names (`moonshotai/kimi-k3`, matching the
# provider's own org naming); which spelling inspect_ai gives its backend for that
# provider is this wrapper's concern. Explicit per provider — never inferred from
# the id's shape.
INSPECT_PROVIDER_REMAP: dict[str, tuple[str, dict[str, str]]] = {
    "moonshotai": ("moonshot", {"MOONSHOTAI_API_KEY": "MOONSHOT_API_KEY",
                                "MOONSHOTAI_BASE_URL": "MOONSHOT_BASE_URL"}),
    # HTTP serving stacks: Inspect's native vllm is in-process and its ollama
    # uses a different client. openai-api reads each service's env vars directly.
    "vllm": ("openai-api/vllm", {}),
    "ollama": ("openai-api/ollama", {}),
}


def inspect_model(model: str) -> str:
    """The model id `inspect_ai.eval` should receive for an ADB model id.

    Params and emitted events keep the canonical ADB id (it is the condition);
    only the eval call sees inspect's spelling. Prepare the environment the way
    Inspect's provider reads it: optional keys in the registry get a placeholder
    bearer when unset, following the chat client's rule, because Inspect's
    providers, like the OpenAI SDK, refuse an empty key. Bridged env vars are copied,
    never overwritten — an explicitly set inspect-native var wins."""
    provider, sep, rest = model.partition("/")
    if not sep:
        return model
    registered = PROVIDERS.get(provider)
    if registered is not None and not registered.api_key.required:
        os.environ.setdefault(registered.api_key.name, "dummy")
    remap = INSPECT_PROVIDER_REMAP.get(provider)
    if remap is None:
        return model
    name, env_bridge = remap
    for src, dst in env_bridge.items():
        if os.environ.get(src) and not os.environ.get(dst):
            os.environ[dst] = os.environ[src]
    return f"{name}/{rest}"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the Inspect task to run: a registry id, a file
    # path (`path/to/file.py@task_fn`), or `pkg:<module>:<attr>` for a @task callable
    # from an installed package (how families select their task).
    task: str = Field(min_length=1)
    # ADB model id (provider/model). `mockllm/model` runs keyless & offline. A
    # self-hosted / OpenAI-compatible server (Ollama, vLLM, …) is selected by the
    # provider's base-URL env var (VLLM_BASE_URL or OLLAMA_BASE_URL), NOT a param —
    # where a model is served is environment, not part of the experimental condition.
    model: str = "mockllm/model"
    # `-T` task args (types.object → a real dict), e.g. {"cot": true}.
    task_args: dict[str, Any] = Field(default_factory=dict)
    # `-M` model args (provider client kwargs).
    model_args: dict[str, Any] = Field(default_factory=dict)
    # generation config overrides, passed to Inspect's generate config,
    # e.g. {"temperature": 0.7, "max_tokens": 1024}.
    generate_args: dict[str, Any] = Field(default_factory=dict)
    # sample cap (0 = all).
    limit: int = Field(default=0, ge=0)
    # repeats of the dataset (1 = one pass).
    epochs: int = Field(default=1, ge=1)
    # provider connection cap (0 = Inspect default).
    max_connections: int = Field(default=0, ge=0)
    # per-sample message cap (0 = none).
    message_limit: int = Field(default=0, ge=0)
    # per-sample token cap (0 = none).
    token_limit: int = Field(default=0, ge=0)
    # forwarded to Inspect's generate config as `seed` (arrives via $ADB_SEED).
    seed: int = 0
