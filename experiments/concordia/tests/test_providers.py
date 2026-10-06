"""The provider table (shared: reps_experiment.providers): pure resolution from
(model id, env) to an endpoint. Exercised from this experiment's suite — the lib
has no test harness of its own yet."""

import pytest

from reps_experiment.providers import PROVIDERS, resolve


def test_openai_env_overrides_default_and_tolerates_no_key():
    ep = resolve("openai/qwen3.5-9b", {"OPENAI_BASE_URL": "http://llama.local:11434/v1"})
    assert ep.base_url == "http://llama.local:11434/v1"
    assert ep.served_model == "qwen3.5-9b"
    assert ep.api_key == "dummy"  # local servers ignore the bearer token
    # nothing set at all: fall back to the registry default, like the OpenAI SDK does
    ep = resolve("openai/gpt-4o", {})
    assert ep.base_url == "https://api.openai.com/v1"


def test_anthropic_mounts_compat_layer_under_v1():
    ep = resolve("anthropic/claude-haiku-4-5-20251001", {"ANTHROPIC_API_KEY": "k"})
    assert ep.base_url == "https://api.anthropic.com/v1"
    assert ep.served_model == "claude-haiku-4-5-20251001"
    # the stored base URL is the plain origin; the /v1 mount is this mapping's job
    ep = resolve("anthropic/claude-haiku-4-5-20251001",
                 {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://proxy.local/"})
    assert ep.base_url == "https://proxy.local/v1"


def test_hosted_providers_have_default_mounts():
    assert resolve("groq/llama-3.3-70b", {"GROQ_API_KEY": "k"}).base_url == \
        "https://api.groq.com/openai/v1"
    assert resolve("google/gemini-2.5-pro", {"GOOGLE_API_KEY": "k"}).base_url == \
        "https://generativelanguage.googleapis.com/v1beta/openai"
    assert resolve("mistral/mistral-small", {"MISTRAL_API_KEY": "k"}).base_url == \
        "https://api.mistral.ai/v1"
    assert resolve("grok/grok-4", {"GROK_API_KEY": "k"}).base_url == "https://api.x.ai/v1"


def test_openrouter_keeps_org_in_served_model():
    ep = resolve("openrouter/qwen/qwen3-coder", {"OPENROUTER_API_KEY": "k"})
    assert ep.served_model == "qwen/qwen3-coder"
    assert ep.base_url == "https://openrouter.ai/api/v1"


def test_azure_takes_the_full_mount_verbatim():
    ep = resolve("azureai/my-gpt5-deployment",
                 {"AZUREAI_API_KEY": "k",
                  "AZUREAI_BASE_URL": "https://myres.openai.azure.com/openai/v1"})
    assert ep.base_url == "https://myres.openai.azure.com/openai/v1"
    assert ep.served_model == "my-gpt5-deployment"
    # no universal origin — the base-url env var is required
    with pytest.raises(ValueError, match="AZUREAI_BASE_URL"):
        resolve("azureai/my-deployment", {"AZUREAI_API_KEY": "k"})


def test_hosted_providers_require_their_key():
    for prefix, p in PROVIDERS.items():
        if not p.api_key.required:
            continue  # openai/: local OpenAI-compatible servers ignore auth
        env = {p.base_url.name: "https://api.example/v1"}
        with pytest.raises(ValueError, match=p.api_key.name):
            resolve(f"{prefix}/some-model", env)


def test_unsupported_or_bare_ids_rejected():
    for bad in ("vertex/gemini-2.5-pro", "foo/bar", "gpt-4o"):
        with pytest.raises(ValueError, match="OpenAI"):
            resolve(bad, {})


def test_malformed_base_url_rejected():
    with pytest.raises(ValueError, match="http"):
        resolve("openai/x", {"OPENAI_BASE_URL": "llama.local:11434"})


