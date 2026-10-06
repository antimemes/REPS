"""Provider prefixes -> OpenAI-compatible endpoints, resolved from the environment.

A model id's provider prefix (``anthropic/claude-…``, ``groq/llama-…``) picks which
credential set to read and which endpoint to talk to; every provider here serves the
OpenAI **/chat/completions** wire protocol, either natively or through a compatibility
mount. Each mapping is declared in ``PROVIDERS`` — explicit per provider, never
inferred from the id's shape. The runner injects the env vars from its credential
store (``reps-runner credentials``); this module only reads them.

No third-party deps. Every canonical provider (the reps-providers package's
registry) is routed: env var names, defaults, and requiredness are consumed
directly, restated nowhere. A provider belongs in the registry only if its
endpoint takes a plain bearer key over HTTP — OAuth-only surfaces (Vertex) have
no entry; degraded-but-correct beats a mapping that half-works. Should a
registry provider ever NOT speak the OpenAI protocol, this client grows an
exclusion — its concern, not the registry's.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from reps_providers import PROVIDERS


@dataclass(frozen=True)
class Endpoint:
    """A resolved backend: where to POST and how to authenticate."""

    provider: str
    served_model: str  # the id with the provider prefix stripped — sent as `model`
    base_url: str      # OpenAI-compat mount (an OpenAI-SDK `base_url`)
    api_key: str


# Where a provider mounts its OpenAI-compat surface when it isn't at the stored
# base itself — exceptions-only, this client's own knowledge (the same shape as
# reps-inspect's name remap): the stored ANTHROPIC_BASE_URL stays the plain API
# origin the rest of the ecosystem expects, and the /v1 mount is appended here.
_MOUNT_SUFFIX = {"anthropic": "/v1"}


def _supported() -> str:
    return ", ".join(f"`{p}/`" for p in PROVIDERS)


def resolve(model_id: str, env: Mapping[str, str] = os.environ) -> Endpoint:
    """Resolve a real (non-mock) model id to an Endpoint, or raise ValueError with an
    actionable message. Every error names the env var or credential set to fix.

    The env var overrides the registry default (proxies, regional endpoints,
    self-hosted servers); a `_MOUNT_SUFFIX` entry is appended to either."""
    provider, sep, served = model_id.partition("/")
    p = PROVIDERS.get(provider) if sep else None
    if p is None:
        raise ValueError(
            f"model {model_id!r}: this experiment's client only speaks the OpenAI "
            f"chat protocol — supported prefixes: {_supported()} (each provider's "
            "OpenAI-compatible endpoint; `openai/<served-name>` reaches any such "
            "server: llama.cpp, vLLM, ollama, or api.openai.com itself), or a "
            "`mock/...` id to run keyless"
        )
    base = (env.get(p.base_url.name) or p.base_url.default).rstrip("/")
    if not base:
        raise ValueError(
            f"model {model_id!r} needs an OpenAI-compatible endpoint but "
            f"${p.base_url.name} is unset — configure the credential set with "
            f"`nix run .#reps-runner -- credentials set {provider}`, or use a mock/ "
            "model to run keyless"
        )
    if not base.startswith(("http://", "https://")):
        raise ValueError(
            f"${p.base_url.name} is {base!r} but must be a full http(s) URL including "
            "scheme, host, port, and any path prefix the server mounts the OpenAI "
            "API under — e.g. http://llama.local:11434/v1 for a local llama.cpp "
            "(the client appends /chat/completions itself)"
        )
    api_key = env.get(p.api_key.name) or ""
    if not api_key:
        if p.api_key.required:
            raise ValueError(
                f"model {model_id!r} needs ${p.api_key.name} — configure the "
                f"credential set with `nix run .#reps-runner -- credentials set "
                f"{provider}`, or use a mock/ model to run keyless"
            )
        api_key = "dummy"  # a bearer token local servers will ignore
    return Endpoint(
        provider=provider,
        served_model=served,
        base_url=base + _MOUNT_SUFFIX.get(provider, ""),
        api_key=api_key,
    )
