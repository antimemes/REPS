"""End-to-end keyless pipeline: a real Concordia simulation on the mock backend.

Skips if Concordia (or its heavy deps) will not import — the pure tests still cover the
boundary. Runs the smallest scenario for one step so it stays fast.
"""

import json

import pytest

pytest.importorskip("concordia")

from concordia_sim.client import RepsLanguageModel  # noqa: E402
from concordia_sim.main import run  # noqa: E402
from concordia_sim.models import Params  # noqa: E402


def _events(event_capture):
    return event_capture.read()


def test_mock_backend_is_deterministic(event_capture):
    a = RepsLanguageModel(Params(default_model="mock/x", seed=3)).sample_text("hello there")
    b = RepsLanguageModel(Params(default_model="mock/x", seed=3)).sample_text("hello there")
    assert a == b and a  # deterministic and non-empty
    event_capture.read()


def test_mock_choice_is_valid_and_emits(event_capture):
    m = RepsLanguageModel(Params(default_model="mock/x", seed=1))
    idx, resp, _ = m.sample_choice("pick one", ["red", "green", "blue"])
    assert 0 <= idx < 3 and resp == ["red", "green", "blue"][idx]
    assert any(e["type"] == "llm.call" for e in _events(event_capture))


def test_anthropic_prefix_targets_compat_endpoint(event_capture, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    m = RepsLanguageModel(Params(default_model="anthropic/claude-haiku-4-5-20251001", seed=1))
    assert m._base_url == "https://api.anthropic.com/v1"
    assert m._served == "claude-haiku-4-5-20251001"
    event_capture.read()


def test_anthropic_base_url_override_gets_v1_mount(event_capture, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.local/")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    m = RepsLanguageModel(Params(default_model="anthropic/claude-haiku-4-5-20251001", seed=1))
    assert m._base_url == "https://proxy.local/v1"
    event_capture.read()


def test_anthropic_without_key_fails_fast(event_capture, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        RepsLanguageModel(Params(default_model="anthropic/claude-haiku-4-5-20251001", seed=1))
    event_capture.read()


def test_azureai_without_base_url_fails_fast(event_capture, monkeypatch):
    # azureai's base has no registry default (per-resource endpoint), so a missing
    # env var is caught at construction, before any turn runs. (openai/ falls back
    # to api.openai.com/v1 — the registry default — so it no longer fails here.)
    monkeypatch.delenv("AZUREAI_BASE_URL", raising=False)
    monkeypatch.setenv("AZUREAI_API_KEY", "k")
    with pytest.raises(RuntimeError, match="AZUREAI_BASE_URL"):
        RepsLanguageModel(Params(default_model="azureai/my-deployment", seed=1))
    event_capture.read()


def test_unsupported_provider_rejected_with_supported_list(event_capture, monkeypatch):
    with pytest.raises(RuntimeError, match=r"anthropic/"):
        RepsLanguageModel(Params(default_model="vertex/gemini-2.5-pro", seed=1))
    event_capture.read()


def test_smoke_mock_run(event_capture, tmp_path, monkeypatch):
    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path))
    run(Params(default_model="mock/model", max_steps=2, seed=7))
    events = _events(event_capture)
    types = [e["type"] for e in events]

    # provenance recorded up front
    prov = [e for e in events if e.get("kind") == "concordia.provenance"]
    assert prov and prov[0]["data"]["agents"] == 2

    # the mock drove real model calls and the run completed with a summary
    assert "llm.call" in types
    metrics = {e["name"]: e["value"] for e in events if e["type"] == "result"}
    assert "status" not in metrics
    assert metrics["agents"] == 2
    assert metrics["model_calls"] > 0
    # the run uses its full step budget — the dialogic game master cannot end the
    # scene early (can_terminate_simulation=False), so max_steps is exact here
    assert metrics["steps"] == 2
    assert metrics["world_events"] > 1

    # the semantic tier: observations delivered per agent (their memory writes,
    # each exactly once) and per-turn perception state — no trawling llm.calls
    obs = [e for e in events if e["type"] == "custom" and e["kind"] == "concordia.observation"]
    assert obs and {e["data"]["agent"] for e in obs} <= {"Alice", "Bob"}
    per_agent = {}
    for e in obs:
        per_agent.setdefault(e["data"]["agent"], []).append(e["data"]["text"])
    for contents in per_agent.values():
        assert len(contents) == len(set(contents))  # no re-emission from the window
    percs = [e for e in events if e.get("kind") == "concordia.perception"]
    assert {e["data"]["agent"] for e in percs} <= {"Alice", "Bob"} and percs

    # Concordia's own log viewer (memories, per-component reasoning) is deposited
    # verbatim as an artifact rather than translated into events
    (art,) = [e for e in events if e.get("kind") == "concordia.artifact"]
    assert art["data"]["path"] == "artifacts/concordia_log.html"
    assert (tmp_path / "artifacts" / "concordia_log.html").stat().st_size > 0


def test_events_conform_to_schema(event_capture, tmp_path, monkeypatch):
    from reps_events.testing import assert_conformant

    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path))
    run(Params(default_model="mock/model", max_steps=1, seed=7))
    assert assert_conformant(_events(event_capture)) > 0


def test_simulation_crash_preserves_partial_results_and_fails(event_capture, tmp_path, monkeypatch):
    import importlib
    from types import SimpleNamespace

    main_module = importlib.import_module("concordia_sim.main")

    class CrashingSimulation:
        def play(self, **kwargs):
            kwargs["step_callback"](SimpleNamespace(step=1, acting_entity="Alice", action="Alice: hello"))
            raise RuntimeError("play crashed")

    monkeypatch.setenv("REPS_RUN_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "build_simulation", lambda params: (CrashingSimulation(), []))
    monkeypatch.setattr(main_module, "version", lambda package: "test")
    with pytest.raises(RuntimeError, match="play crashed"):
        main_module.run(Params(default_model="mock/model", max_steps=2, seed=7))
    events = _events(event_capture)
    metrics = {e["name"]: e["value"] for e in events if e["type"] == "result"}
    assert metrics["steps"] == 1
    assert metrics["world_events"] >= 1
    assert "status" not in metrics
    assert any(e["type"] == "log" and e["level"] == "error" for e in events)
