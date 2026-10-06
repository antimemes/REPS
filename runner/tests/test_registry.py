"""The canonical provider registry (the reps-providers package): parsing and
validation hold the invariants consumers lean on."""

from reps_providers import MOCK_PREFIXES, PROVIDERS


def test_registry_shape():
    assert MOCK_PREFIXES == {"mock", "mockllm"}
    for name, provider in PROVIDERS.items():
        assert provider.api_key.name.endswith(("_API_KEY", "_TOKEN")), name
        assert provider.base_url.name.endswith("_BASE_URL"), name
    # OpenAI-compatible local servers may ignore auth.
    assert {n for n, p in PROVIDERS.items() if not p.api_key.required} == {
        "openai", "vllm", "ollama"
    }
    assert all(not PROVIDERS[n].base_url.default for n in ("vllm", "ollama"))
    # and its base default is a real runtime fallback, like the OpenAI SDK's own
    assert PROVIDERS["openai"].base_url.default == "https://api.openai.com/v1"


def test_azure_requires_resource_endpoint_and_key():
    azure = PROVIDERS["azure"]
    assert azure.api_key.name == "AZURE_OPENAI_API_KEY"
    assert azure.api_key.required
    assert azure.base_url.name == "AZURE_OPENAI_BASE_URL"
    assert azure.base_url.default == ""
