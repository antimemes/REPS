#!/usr/bin/env python3
"""Regenerate model_catalog.json — the provider model catalog (`types.llm` hints).

This is PROVIDER knowledge, deliberately not wrapper knowledge: which models
each provider serves, under which exact ids. It lives beside the runner's
provider registry conceptually — any wrapper (inspect today, others later)
builds its own suggestion syntax over it; only suggestions.nix's value
formatting follows inspect's provider-prefix convention.

One command, no hand edits:  task models:update  (or run this file directly).

Two public catalogs are joined:
  - models.dev/api.json ........ the curated current lineup per provider, with
                                 per-model metadata (name, release_date, modalities)
  - LiteLLM's model_prices json  the largest public index of *dated snapshot* ids
                                 (gpt-4o-2024-11-20, claude-sonnet-4-5-20250929, ...)

models.dev decides WHICH models exist; LiteLLM upgrades each id to its newest
dated snapshot when one exists (policy: hints are fully-qualified wherever the
provider mints dated ids, so a condition pins the snapshot, not a moving alias).
The public catalogs lag on snapshots for the newest models, so two first-party
sources fill the gap: (1) the provider SDKs' own model-id literal types,
fetched anonymously from their public source — the provider's authored list of
every accepted id, which both supplies dated ids the catalogs missed and
proves when a new model's canonical id is genuinely undated (Anthropic's
newest are); (2) the provider's authenticated /models endpoint, when a key is
available in the environment or the reps runner's credential store
(`reps-runner credentials` / ~/.config/reps/credentials.toml). Keyless runs are
first-class: SDK literals cover the id-form question without credentials.
Providers listed here are the API-backed providers with a first-party enumerable
model list and a declared mapping in at least one wrapper (an explicit map,
deliberately not discovered) — first-class in the `types.llm` vocabulary whether
they have a dedicated SDK (anthropic, groq, …) or are reached over an
OpenAI-compatible mount (moonshotai); which wire a wrapper uses is that wrapper's
concern, not the user's. openrouter is catalog-backed too — its public /models
list is keyless and first-party in the strict sense (the ids its API accepts,
already org-scoped: openrouter/moonshotai/kimi-k3) — but has no dated-snapshot
scheme or models.dev lineup, so it bypasses that machinery (openrouter_models).
Pattern-style providers (azureai deployments, openai-api passthrough) are static
hints in suggestions.nix instead, since they have no enumerable model list; the
openrouter/ pattern hint stays alongside the catalog entries as the escape hatch
for models OpenRouter added since the last regen.

Output is deterministic for unchanged upstream data (no timestamps): a clean
`git diff` after running IS the update.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# the canonical provider registry (the reps-providers package's providers.toml:
# names + credential templates) — this catalog is a LAYER over it: foreign catalog
# spellings and /models endpoints are declared here; provider names, env var
# names, and hints are the registry's facts. Read directly (this script runs as
# bare python3, deliberately dependency-free).
_REGISTRY = tomllib.loads(
    (Path(__file__).resolve().parents[1]
     / "reps-providers" / "reps_providers" / "providers.toml").read_text()
)
SECRET_VARS: dict[str, str] = {
    name: p["api_key"]["name"] for name, p in _REGISTRY["providers"].items()
}

MODELS_DEV_URL = "https://models.dev/api.json"
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True, kw_only=True)
class _Source:
    """Where a canonical provider's model list lives in the FOREIGN catalogs —
    their spellings (models.dev says `xai`, litellm says `gemini`), never ours."""

    models_dev: str
    litellm: str | None


PROVIDERS: dict[str, _Source] = {
    "anthropic": _Source(models_dev="anthropic", litellm="anthropic"),
    "openai": _Source(models_dev="openai", litellm="openai"),
    "google": _Source(models_dev="google", litellm="gemini"),
    "groq": _Source(models_dev="groq", litellm="groq"),
    "mistral": _Source(models_dev="mistral", litellm="mistral"),
    "grok": _Source(models_dev="xai", litellm="xai"),
    # moonshotai = the global api.moonshot.ai endpoint (models.dev's moonshotai-cn
    # is the .cn mainland deployment — separate lineup, not listed)
    "moonshotai": _Source(models_dev="moonshotai", litellm="moonshot"),
}


@dataclass(frozen=True, kw_only=True)
class _ModelsApi:
    """A provider's live /models endpoint, queried only when its key (the registry's
    secret var — never redeclared here) is available: the public catalogs lag on
    dated snapshot ids for the newest models, the providers don't. Google's REST
    list takes the key as a query param."""

    url: str
    auth: str = "bearer"


PROVIDER_APIS: dict[str, _ModelsApi] = {
    "anthropic": _ModelsApi(url="https://api.anthropic.com/v1/models?limit=1000",
                            auth="anthropic"),
    "openai": _ModelsApi(url="https://api.openai.com/v1/models"),
    "google": _ModelsApi(url="https://generativelanguage.googleapis.com/v1beta/models",
                         auth="google"),
    "groq": _ModelsApi(url="https://api.groq.com/openai/v1/models"),
    "mistral": _ModelsApi(url="https://api.mistral.ai/v1/models"),
    "grok": _ModelsApi(url="https://api.x.ai/v1/models"),
    "moonshotai": _ModelsApi(url="https://api.moonshot.ai/v1/models"),
}

for _p in list(PROVIDERS) + list(PROVIDER_APIS):
    assert _p in SECRET_VARS, f"{_p}: catalog layer names a non-canonical provider"


def credential_hint(prefix: str) -> str:
    """The GUI hint for a provider — derived from the registry, never hand-written."""
    return f"Needs {SECRET_VARS[prefix]} (asked for on first run)."

# First-party, public, keyless id lists — each provider's own declaration of the
# ids its API accepts, as (url, extraction regex) pairs. SDK literal-type modules
# where the SDK ships them (anthropic, openai); the provider's official docs in
# their machine-readable form otherwise (llms.txt-style markdown, or the docs HTML
# for google, whose devsite serves no markdown). These are authoritative in a way
# community catalogs are not: LiteLLM has shipped dated ids providers 404 on.
FIRST_PARTY_ID_SOURCES = {
    "anthropic": [(
        "https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/src/anthropic/types/model.py",
        r'"([a-z0-9][a-z0-9._/-]*)"',
    )],
    "openai": [(
        "https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/shared/chat_model.py",
        r'"([a-z0-9][a-z0-9._/-]*)"',
    )],
    "google": [(
        "https://ai.google.dev/gemini-api/docs/models",
        r"\b(gemini-[0-9][a-zA-Z0-9.-]+|gemma-[0-9a-z.-]+)\b",
    )],
    "groq": [(
        "https://console.groq.com/docs/models.md",
        r"/docs/model/([a-z0-9._/-]+)\)|\b(groq/compound(?:-mini)?)\b",
    )],
    "mistral": [(
        "https://docs.mistral.ai/llms-full.txt",
        r"`([a-z][a-z0-9.-]*(?:-latest|-2[0-9]{3}))`",
    )],
    "grok": [(
        "https://docs.x.ai/docs/models.md",
        r"\b(grok-[a-z0-9.-]+)\b",
    )],
}

DATE_SUFFIX = re.compile(r"^(?P<stem>.+?)[-@](?P<date>20\d{2}-?\d{2}-?\d{2})$")
# mistral versions snapshots as -YYMM (mistral-small-2603); "-latest" is its moving alias
MISTRAL_SUFFIX = re.compile(r"^(?P<stem>.+?)-(?P<date>2\d{3})$")
BEDROCK_REGION = re.compile(r"^(?:us|eu|apac|jp|au|ca|global)\.")


def fetch(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "reps-model-catalog-updater", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def stored_credentials() -> dict[str, str]:
    """Env-var -> value from the reps runner's credential store (never printed).

    Mirrors reps_runner.credentials.config_path(): $REPS_CREDENTIALS_FILE overrides,
    else $XDG_CONFIG_HOME/reps/credentials.toml, else ~/.config/reps/credentials.toml.
    """
    import tomllib

    override = os.environ.get("REPS_CREDENTIALS_FILE")
    if override:
        path = Path(override)
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = Path(base) / "reps" / "credentials.toml"
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text())
    return {
        k: str(v)
        for vals in data.values()
        if isinstance(vals, dict)
        for k, v in vals.items()
    }


def first_party_doc_ids(prefix: str) -> list[str]:
    """Model ids from the provider's own public declarations, [] on failure."""
    ids: list[str] = []
    for url, pattern in FIRST_PARTY_ID_SOURCES.get(prefix, []):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "reps-model-catalog-updater"})
            with urllib.request.urlopen(req, timeout=60) as r:
                source = r.read().decode(errors="replace")
        except OSError:
            continue
        for match in re.findall(pattern, source):
            groups = [match] if isinstance(match, str) else list(match)
            ids.extend(g for g in groups if g)
    return sorted(set(ids))


def live_provider_ids(prefix: str, stored: dict[str, str]) -> list[str]:
    """The provider's own current model ids, [] when no key is configured."""
    if prefix not in PROVIDER_APIS:
        return []
    api = PROVIDER_APIS[prefix]
    env = SECRET_VARS[prefix]
    key = os.environ.get(env, "") or stored.get(env, "")
    if not key:
        return []
    if api.auth == "anthropic":
        data = fetch(api.url, {"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif api.auth == "google":
        data = fetch(f"{api.url}?key={key}")
        return [m["name"].removeprefix("models/") for m in data.get("models", [])]
    else:
        data = fetch(api.url, {"Authorization": f"Bearer {key}"})
    return [m["id"] for m in data.get("data", [])]


def openrouter_models() -> list[dict]:
    """OpenRouter's public /models list (keyless) — the ids its API accepts.

    Ids arrive already org-scoped (moonshotai/kimi-k3), and there is no
    dated-snapshot scheme, so none of the lineup/snapshot machinery applies.
    `~`-prefixed ids are OpenRouter's retired/rerouted aliases — excluded like
    -latest aliases elsewhere. `:free` variants stay: a distinct serving tier is
    a legitimately selectable (and distinct) condition. `created` is the stable
    added-to-OpenRouter timestamp — the closest thing to a release date, and
    deterministic across regens."""
    import time

    models = []
    for m in fetch(OPENROUTER_URL).get("data", []):
        arch = m.get("architecture") or {}
        inputs = arch.get("input_modalities") or []
        outputs = arch.get("output_modalities") or []
        if "text" not in inputs or set(outputs) != {"text"}:
            continue
        if m["id"].startswith("~"):
            continue
        created = m.get("created") or 0
        models.append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "release_date": time.strftime("%Y-%m-%d", time.gmtime(created)) if created else "",
        })
    return sorted(models, key=lambda m: (m["release_date"], m["id"]), reverse=True)


def text_chat_model(m: dict) -> bool:
    mod = m.get("modalities") or {}
    inputs, outputs = mod.get("input") or [], mod.get("output") or []
    return "text" in inputs and set(outputs) == {"text"}


def dated_snapshots(litellm: dict) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(litellm_provider, undated stem) -> [(date, dated id), ...]"""
    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, meta in litellm.items():
        if meta.get("mode") not in ("chat", "responses"):
            continue
        provider = meta.get("litellm_provider")
        m = DATE_SUFFIX.match(key.rsplit("/", 1)[-1])
        if provider and m:
            out.setdefault((provider, m["stem"]), []).append((m["date"].replace("-", ""), key.rsplit("/", 1)[-1]))
    return out


def stem_of(prefix: str, model_id: str) -> str:
    """The id with any snapshot qualifier or moving-alias suffix removed."""
    if m := DATE_SUFFIX.match(model_id):
        return m["stem"]
    if prefix == "mistral":
        if model_id.endswith("-latest"):
            return model_id.removesuffix("-latest")
        if m := MISTRAL_SUFFIX.match(model_id):
            return m["stem"]
    if model_id.endswith("-latest"):
        return model_id.removesuffix("-latest")
    return model_id


def qualified_id(prefix: str, mdev_id: str, snapshots: dict[str, list[tuple[str, str]]]) -> str:
    """Newest dated snapshot for this model, or the id as-is when none is known."""
    if DATE_SUFFIX.match(mdev_id):
        return mdev_id
    candidates = snapshots.get(stem_of(prefix, mdev_id), [])
    return max(candidates)[1] if candidates else mdev_id


def main() -> int:
    models_dev, litellm = fetch(MODELS_DEV_URL), fetch(LITELLM_URL)
    ll_snapshots = dated_snapshots(litellm)
    stored = stored_credentials()

    providers = {}
    keyless = []
    for prefix, source in PROVIDERS.items():
        # First-party id lists: SDK literals (keyless) + the live /models endpoint
        # (when a key is available). When ANY first-party list exists it is the
        # EXCLUSIVE snapshot source — community catalogs have shipped dated ids the
        # provider's API 404s on (claude-opus-4-6-20260205), so litellm only fills
        # in for providers with no first-party list at all.
        doc_ids = first_party_doc_ids(prefix)
        live = live_provider_ids(prefix, stored)
        first_party = doc_ids + live
        snapshots: dict[str, list[tuple[str, str]]] = {}
        if first_party:
            for fp_id in first_party:
                if m := DATE_SUFFIX.match(fp_id):
                    snapshots.setdefault(m["stem"], []).append((m["date"].replace("-", ""), fp_id))
        else:
            for (ll_provider, stem), dated in ll_snapshots.items():
                if ll_provider == source.litellm:
                    snapshots.setdefault(stem, []).extend(dated)
        if doc_ids:
            print(f"{prefix}: first-party id list used ({len(doc_ids)} ids)")
        if live:
            print(f"{prefix}: live /models list used ({len(live)} ids)")
        elif prefix in PROVIDER_APIS and not doc_ids:
            keyless.append(prefix)

        lineup = [m for m in models_dev[source.models_dev]["models"].values() if text_chat_model(m)]
        models = [
            {
                "id": qualified_id(prefix, m["id"], snapshots),
                "name": m.get("name", m["id"]),
                "release_date": m.get("release_date", ""),
            }
            for m in lineup
        ]
        # a dated id, its undated alias, and any -latest alias are the same model:
        # keep one entry, preferring the qualified id (sorted last within a stem)
        by_stem = {}
        for m in sorted(
            models,
            key=lambda m: (m["release_date"], 0 if m["id"].endswith("-latest") else 1, m["id"]),
        ):
            by_stem[stem_of(prefix, m["id"])] = m
        # ... then expand each model to EVERY dated snapshot the provider serves
        # (gpt-4o has three; each is a distinct pinned artifact and a legitimate
        # condition) — the model's entry above already carries the newest
        for stem, m in list(by_stem.items()):
            for date, snap_id in sorted(set(snapshots.get(stem, [])), reverse=True):
                if snap_id != m["id"]:
                    by_stem[snap_id] = {
                        "id": snap_id,
                        "name": m["name"],
                        "release_date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                    }
        # every model is surfaced (the GUI combobox is type-to-filter; curation is
        # the user's job, not the catalog's), sorted newest first
        models = sorted(by_stem.values(), key=lambda m: (m["release_date"], m["id"]), reverse=True)
        # visibility tripwire: an id the provider's own list doesn't contain is
        # either lag in models.dev or a bogus community id — say so
        fp_set = set(first_party)
        for m in models:
            if fp_set and m["id"] not in fp_set:
                print(f"note: {prefix}/{m['id']} absent from the first-party id list")
        providers[prefix] = {"credential_hint": credential_hint(prefix), "models": models}

    or_models = openrouter_models()
    print(f"openrouter: first-party /models list used ({len(or_models)} ids)")
    providers["openrouter"] = {
        "credential_hint": credential_hint("openrouter"),
        "models": or_models,
    }

    out = {
        "_generated_by": "lib/model-catalog/update.py — do not edit by hand",
        "sources": [MODELS_DEV_URL, LITELLM_URL, OPENROUTER_URL],
        "providers": providers,
    }
    path = Path(__file__).resolve().parent / "model_catalog.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    total = sum(len(p["models"]) for p in providers.values())
    print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}: "
          f"{total} models across {len(providers)} providers")
    if keyless:
        print(
            f"WARNING: no first-party id source for {', '.join(keyless)} (no SDK literal "
            f"list, no credentials) — their newest ids may be undated aliases. Configure "
            f"keys (`nix run .#reps-runner -- credentials set ...`) and rerun to check them "
            f"against the providers' own /models endpoints."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
