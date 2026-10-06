"""Param validation and model routing at the Inspect boundary."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from adb_inspect.main import eval_kwargs
from adb_inspect.models import Params, inspect_model


@pytest.mark.parametrize("model,expected", [
    ("vllm/Qwen/Qwen2.5-7B-Instruct", "openai-api/vllm/Qwen/Qwen2.5-7B-Instruct"),
    ("ollama/qwen2.5:7b-instruct-fp16", "openai-api/ollama/qwen2.5:7b-instruct-fp16"),
])
def test_selfhosted_remap_preserves_canonical_params(model, expected, tmp_path, monkeypatch):
    monkeypatch.delenv(f"{model.partition('/')[0].upper()}_API_KEY", raising=False)
    params = Params(task="fixture", model=model)
    assert inspect_model(model) == expected
    assert eval_kwargs(params, tmp_path)["model"] == expected
    assert params.model == model


@pytest.mark.parametrize("provider,served", [
    ("vllm", "Qwen/Qwen2.5-7B-Instruct"),
    ("ollama", "qwen2.5:7b-instruct-fp16"),
])
@pytest.mark.parametrize("key_source", ["absent", "environment", "model_args", "both"])
def test_selfhosted_inspect_uses_service_endpoint_and_optional_key(
    provider, served, key_source, monkeypatch, tmp_path
):
    from inspect_ai.model import get_model
    from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

    base_var = f"{provider.upper()}_BASE_URL"
    key_var = f"{provider.upper()}_API_KEY"
    monkeypatch.setenv(base_var, "http://localhost:8000/v1")
    monkeypatch.delenv(key_var, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://wrong.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-key")
    model_args = {}
    if key_source in {"environment", "both"}:
        monkeypatch.setenv(key_var, "service-key")
    if key_source in {"model_args", "both"}:
        model_args["api_key"] = "explicit-key"
    params = Params(task="fixture", model=f"{provider}/{served}", model_args=model_args)
    kw = eval_kwargs(params, tmp_path)
    model = get_model(kw["model"], memoize=False, **kw["model_args"])
    assert type(model.api) is OpenAICompatibleAPI
    assert model.api.service_model_name() == served
    assert model.api.base_url == "http://localhost:8000/v1"
    assert model.api.api_key == {
        "absent": "dummy", "environment": "service-key",
        "model_args": "explicit-key", "both": "explicit-key"
    }[key_source]
    assert params.model_args == model_args
    assert params.model == f"{provider}/{served}"


def test_required_key_provider_receives_no_dummy_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    params = Params(task="fixture", model="anthropic/model")
    kw = eval_kwargs(params, tmp_path)
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert kw["model_args"] == {}
    assert kw["model_args"] is params.model_args


def test_openai_optional_key_gets_placeholder_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    params = Params(task="fixture", model="openai/model")
    kw = eval_kwargs(params, tmp_path)
    assert os.environ["OPENAI_API_KEY"] == "dummy"
    assert kw["model_args"] == {}
    assert kw["model_args"] is params.model_args
    monkeypatch.setenv("OPENAI_API_KEY", "existing-key")
    assert inspect_model(params.model) == params.model
    assert os.environ["OPENAI_API_KEY"] == "existing-key"


@pytest.mark.parametrize("model", ["openai", "vllm", "ollama"])
def test_bare_model_name_leaves_environment_unset(model, monkeypatch):
    key_var = f"{model.upper()}_API_KEY"
    monkeypatch.delenv(key_var, raising=False)
    assert inspect_model(model) == model
    assert key_var not in os.environ


def test_defaults_are_keyless_mock():
    p = Params(task="pkg:hello_task:hello")
    assert p.model == "mockllm/model"
    assert p.task_args == {} and p.generate_args == {}
    assert p.limit == 0 and p.epochs == 1 and p.seed == 0


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Params(task="pkg:hello_task:hello", bogus=1)


def test_task_required_nonempty():
    with pytest.raises(ValidationError):
        Params(task="")


def test_negative_limits_rejected():
    with pytest.raises(ValidationError):
        Params(task="x", limit=-1)
    with pytest.raises(ValidationError):
        Params(task="x", epochs=0)
