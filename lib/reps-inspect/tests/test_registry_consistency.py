"""The inspect bridge is a TENANT layer over the canonical provider registry
(the reps-providers package): exceptions-only (identity is the default), referencing
canonical names and env vars, never inventing them. This check keeps the remap from
drifting away from the registry — a key that isn't canonical, or a bridged env var
the registry never declares, is a bug here, not a registry change. The TOML is read
by path: a tenant check doesn't warrant a package dependency."""

import tomllib
from pathlib import Path

from reps_inspect.models import INSPECT_PROVIDER_REMAP


def _registry() -> dict:
    return tomllib.loads(
        (Path(__file__).resolve().parents[2]
         / "reps-providers" / "reps_providers" / "providers.toml").read_text()
    )


def test_remap_references_only_canonical_providers_and_vars():
    reg = _registry()
    assert "mockllm" in reg["mock_prefixes"]
    for prefix, (inspect_name, env_bridge) in INSPECT_PROVIDER_REMAP.items():
        provider = reg["providers"].get(prefix)
        assert provider is not None, f"{prefix}: remapped but not canonical"
        assert inspect_name != prefix, f"{prefix}: identity remap is dead weight"
        declared = {provider["api_key"]["name"], provider["base_url"]["name"]}
        for src in env_bridge:
            assert src in declared, f"{prefix}: bridges {src}, not in the registry"
