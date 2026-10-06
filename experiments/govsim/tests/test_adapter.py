import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from reps_events.render import hint_paths
from reps_events.testing import assert_conformant
from reps_providers import PROVIDERS
from reps_testing import assert_run_has_no_secrets
from govsim_adapter.models import Payload

from govsim_adapter.main import EXPERIMENTS, Params
from govsim_adapter.metrics import compute_metrics, gini
from govsim_adapter.embedder import HashEmbedder


def params(**overrides):
    return dict(experiment="fish_baseline_concurrent", max_rounds=1,
                model="mock/model", embedder="hash", temperature=0.0,
                top_p=1.0, max_tokens=8000) | overrides


@pytest.mark.parametrize("override", [dict(max_rounds=-1), dict(max_tokens=0),
    dict(reasoning_effort="none"), dict(top_p=0), dict(top_p=1.1), dict(temperature=float("nan")),
    dict(embedder="other"), dict(threads=0), dict(threads=-1), dict(threads=1.5),
    dict(threads=True), dict(experiment="fish_baseline_concurrent,seed=9")])
def test_invalid_params(override):
    with pytest.raises(ValidationError):
        Params(**params(**override))


def test_embedder_deterministic_normalized():
    a, b = HashEmbedder(), HashEmbedder()
    assert np.array_equal(a.embed("fish"), b.embed("fish"))
    assert np.linalg.norm(a.embed("fish")) == pytest.approx(1)
    assert not np.array_equal(a.embed("fish"), a.embed_retrieve("fish"))


def test_installed_upstream_packages_and_config_resources():
    from importlib.metadata import distribution
    from importlib.resources import files
    from govsim_adapter.main import _compose

    installed = distribution("govsim")
    roots = {str(path).split("/")[0] for path in installed.files}
    assert "simulation" in roots
    assert not {"utils", "subskills", "pathfinder"}.intersection(roots)
    assert files("simulation.conf").joinpath("config.yaml").is_file()
    config = _compose(Params(**params(experiment="pollution_perturbation_outsider", max_rounds=0)), seed=37)
    assert config.experiment.env.max_num_rounds == 15
    assert config.seed == 37


def test_child_threads_and_pinned_embedder(tmp_path, event_capture):
    from govsim_adapter.embedder import MXBAI_MODEL, MXBAI_REVISION

    config = tmp_path / "params.json"
    config.write_text(json.dumps(params(threads=3, embedder="mxbai")))
    script = '''
import importlib.abc
import json
import os
import sys
from govsim_adapter.main import main
from govsim_adapter.embedder import HashEmbedder
assert not {"numpy", "torch", "transformers"}.intersection(sys.modules)

class NumericImportGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {"numpy", "torch", "transformers"}:
            for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                assert os.environ[key] == "3", key
            assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
        return None
sys.meta_path.insert(0, NumericImportGuard())

# Substitute the download/encoder only, after the import guard has checked the
# real bootstrap. Exercise the pinned constructor and upstream retrieval method.
class Encoder:
    def __init__(self, model, **kwargs):
        print("ENCODER " + json.dumps({"model": model, **kwargs}))
    def encode(self, text, **kwargs):
        return HashEmbedder().embed(text)

import govsim_adapter.embedder as adapter
original = adapter.make_embedder
def make_embedder(kind):
    import sentence_transformers
    sentence_transformers.SentenceTransformer = Encoder
    return original(kind)
adapter.make_embedder = make_embedder
status = main()
import torch
print("THREADS " + str(torch.get_num_threads()))
raise SystemExit(status)
'''
    proc = subprocess.run([sys.executable, "-c", script, str(config)], cwd=tmp_path,
                          env=dict(os.environ, REPS_RUN_DIR=str(tmp_path), REPS_SEED="37",
                                   HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"),
                          text=True, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "UnsupportedFieldAttributeWarning" not in proc.stderr
    assert "Defaults list is missing" not in proc.stderr
    assert "THREADS 3" in proc.stdout.splitlines()
    encoder = next(json.loads(line.removeprefix("ENCODER "))
                   for line in proc.stdout.splitlines() if line.startswith("ENCODER "))
    assert encoder == {"model": MXBAI_MODEL, "device": "cpu", "revision": MXBAI_REVISION}
    config_event = next(e for e in event_capture.read() if e.get("kind") == "govsim.config")
    assert config_event["data"]["threads"] == 3
    assert config_event["data"]["embedder_revision"] == MXBAI_REVISION


def test_collapse_and_outsider_denominator():
    rows = [dict(action="harvesting", round=0, agent_id=i,
                 resource_collected=5, wanted_resource=11,
                 resource_in_pool_before_harvesting=100,
                 resource_in_pool_after_harvesting=75) for i in range(5)]
    result = compute_metrics(pd.DataFrame(rows), 12)
    assert result == dict(rounds=1, collapsed=False, survival_months=12,
                         total_harvest=25, gain_per_agent=5.0,
                         final_resource=75, equality=1.0, over_usage=1.0)
    for row in rows:
        row["resource_in_pool_after_harvesting"] = 4
    assert compute_metrics(pd.DataFrame(rows), 12)["survival_months"] == 1
    assert compute_metrics(pd.DataFrame(rows), 12)["collapsed"] is True
    assert gini(np.array([0, 0])) == 0


@pytest.mark.parametrize("text,completion,returned", [
    (" answer ", " answer ", "answer"),
    ("Rating: 9\n\n", "Rating: 9\n\n", "Rating: 9"),
    ("<think>Choose five.</think>\n\nAnswer: 5.", "\n\nAnswer: 5.", "Answer: 5."),
    pytest.param("</think>Answer: 5.", "</think>Answer: 5.", "</think>Answer: 5.",
                 id="lone-close-at-start"),
    pytest.param("\n</think>Answer: 5.", "\n</think>Answer: 5.", "</think>Answer: 5.",
                 id="lone-close-after-newline"),
    pytest.param("Answer: </think>5.", "Answer: </think>5.", "Answer: </think>5.",
                 id="lone-close-in-middle"),
    pytest.param("Answer: 5.</think>", "Answer: 5.</think>", "Answer: 5.</think>",
                 id="lone-close-at-end"),
    pytest.param("<think>Answer: 5.", "<think>Answer: 5.", "<think>Answer: 5.",
                 id="unclosed-think-at-start"),
    pytest.param("\n<think>Answer: 5.", "\n<think>Answer: 5.", "<think>Answer: 5.",
                 id="unclosed-think-after-newline"),
    pytest.param("Answer: <think>5.", "Answer: <think>5.", "Answer: <think>5.",
                 id="unclosed-think-in-middle"),
    pytest.param("Answer: 5.<think>", "Answer: 5.<think>", "Answer: 5.<think>",
                 id="unclosed-think-at-end"),
    pytest.param("<thinking>x</thinking>y", "<thinking>x</thinking>y", "<thinking>x</thinking>y",
                 id="different-tag-stays-literal"),
    pytest.param("a<think>x</think>b", "ab", "ab", id="surrounding-text-concatenated"),
    pytest.param('<think signature="s">x</think>y', "y", "y", id="think-with-attributes"),
    pytest.param("<think>x</think>a<think>y</think>b", "a<think>y</think>b", "a<think>y</think>b",
                 id="only-first-block-extracted"),
    ("", "", ""),
])
def test_backend_forwards_sampling_and_caps_tokens(monkeypatch, event_capture, text, completion, returned):
    from govsim_adapter.backend import ChatClientBackend
    backend = ChatClientBackend("mock/model", 42, temperature=0.2, max_tokens=64)
    # Replace transport only: exercise the real adapter and shared client's
    # generation policy without making any network request.
    captured = {}
    def transport(kw):
        captured.update(kw)
        return _reply(text)
    backend.client.is_mock = False
    backend.client._request = transport
    chat = [{"role": "user", "content": "test"},
            {"role": "assistant", "content": "Answer: "}]
    assert backend.request_api(chat, 0.9, 0.75, 8000) == returned
    assert captured["model"] == "model"
    assert captured["temperature"] == 0.2
    assert captured["top_p"] == 0.75
    assert captured["max_completion_tokens"] == 64
    assert captured["seed"] == 42
    assert captured["messages"][-1] == {"role": "assistant", "content": "Answer:"}
    assert chat[-1]["content"] == "Answer: "
    assert backend.client.n_calls == 1
    [event] = event_capture.read()
    assert event["agent"] == "framework"
    assert event["output"]["completion"] == completion
    assert event["call"]["response"]["choices"][0]["message"]["content"] == text


@pytest.mark.parametrize("echo_prefix", [True, False])
def test_mistral_prefill_response_parses_through_pathfinder(echo_prefix, event_capture):
    from pathfinder import assistant, gen, select, user
    from govsim_adapter.backend import ChatClientBackend

    captured = []
    def respond(messages):
        captured.append(messages)
        return ("Answer: " if echo_prefix else "") + "We should share.\nRating: 5"

    backend = ChatClientBackend("mock/mistral-medium-3-5", 42, mock_responder=respond)
    lm = backend
    with user():
        lm += "Give a reason and a rating."
    with assistant():
        lm += "Answer: "
        lm += gen(name="reason", stop_regex=r"\.")
        lm += ".\nRating: "
        lm += select([str(i) for i in range(1, 11)], name="rating")
    assert lm["reason"].strip() == "We should share"
    assert lm["rating"] == "5"
    assert len(captured) == 1  # gen and select share one completion buffer
    assert captured[0][-1] == {"role": "assistant", "content": "Answer:", "prefix": True}
    assert "prefix" not in lm.chat[-1]


@pytest.mark.parametrize("name,query,agent", [
    ("John", "prompt_harvest", "persona_0"),
    ("framework", "prompt_summarize_conversation_in_one_sentence", "framework/summarize_conversation"),
    ("framework", "prompt_find_harvesting_limit_from_conversation", "framework/find_harvesting_limit"),
    ("framework", "prompt_text_to_triple", "framework/text_to_triple"),
])
def test_logger_attributes_queries_without_changing_api_request(monkeypatch, event_capture, name, query, agent):
    from simulation.utils import WandbLogger
    from govsim_adapter.backend import ChatClientBackend
    from govsim_adapter.logger import RepsLogger

    # Replace wandb work only: use the real logger hooks, backend and event emitter.
    monkeypatch.setattr(WandbLogger, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(WandbLogger, "get_agent_chain", lambda *args: None)
    monkeypatch.setattr(WandbLogger, "start_chain", lambda *args: None)
    backend = ChatClientBackend("mock/model", 42, mock_responder=lambda _: "5")
    logger = RepsLogger("test", {"experiment": {"personas": {
        "num": 1, "persona_0": {"name": "John"},
    }}}, backend=backend)
    logger.get_agent_chain(name, "phase")
    logger.start_chain(f"phase::{query}")
    backend.request_api([{"role": "user", "content": "test"}], 0, 1, 64)
    [event] = event_capture.read()
    assert event["agent"] == agent
    assert event["metadata"]["govsim.phase"] == "phase"
    assert event["metadata"]["govsim.query"] == query
    assert "metadata" not in event["call"]["request"]


@pytest.mark.parametrize("temperature,top_p,reasoning_effort", [
    (None, None, "low"), (None, 0.75, None), (0.2, None, None),
])
def test_backend_explicit_null_omits_upstream_sampling_defaults(
    temperature, top_p, reasoning_effort, event_capture,
):
    from govsim_adapter.backend import ChatClientBackend
    parsed = Params(**params(temperature=temperature, top_p=top_p,
                             reasoning_effort=reasoning_effort))
    backend = ChatClientBackend("mock/model", 42,
                                temperature=parsed.temperature,
                                top_p=parsed.top_p,
                                reasoning_effort=parsed.reasoning_effort,
                                max_tokens=64)
    captured = {}
    def transport(kw):
        captured.update(kw)
        return _reply("5")
    backend.client.is_mock = False
    backend.client._request = transport
    # Upstream select forces 0.0/1.0 even when the wrapper receives None.
    assert backend.request_api([{"role": "user", "content": "test"}], 0.0, 1.0, 8000) == "5"
    assert ("temperature" in captured) == (temperature is not None)
    assert ("top_p" in captured) == (top_p is not None)
    assert ("reasoning_effort" in captured) == (reasoning_effort is not None)
    if reasoning_effort is not None:
        assert captured["reasoning_effort"] == reasoning_effort
    assert captured["max_completion_tokens"] == 64


@pytest.mark.parametrize("experiment", EXPERIMENTS)
def test_upstream_mock_pipeline(tmp_path, experiment, event_capture, warmed_run):
    proc = _run_mock_pipeline(tmp_path, warmed_run, experiment=experiment, temperature=None,
                              top_p=None, reasoning_effort="low")
    assert proc.returncode == 0, proc.stderr
    events = event_capture.read()
    metrics = {e["name"]: e["value"] for e in events if e["type"] == "result"}
    assert "status" not in metrics
    assert metrics["rounds"] == 1
    assert metrics["total_harvest"] == (20 if "outsider" in experiment else 25)
    assert metrics["equality"] == 1.0
    assert "model_calls" not in metrics
    calls = [e for e in events if e["type"] == "llm.call"]
    assert calls
    allowed_agents = {*(f"persona_{i}" for i in range(5)),
                      "framework/summarize_conversation", "framework/find_harvesting_limit",
                      "framework/text_to_triple"}
    assert {e["agent"] for e in calls} <= allowed_agents
    assert all(e["metadata"]["govsim.phase"] and e["metadata"]["govsim.query"] for e in calls)
    assert all(set(e["metadata"]) == {"govsim.phase", "govsim.query"} for e in calls)
    assert "UnsupportedFieldAttributeWarning" not in proc.stderr
    assert "Defaults list is missing" not in proc.stderr
    assert any(e["type"] == "custom" and e["kind"] == "govsim.state" for e in events)
    from omegaconf import OmegaConf
    config = OmegaConf.create(next(e["data"] for e in events if e.get("kind") == "govsim.config"))
    assert config.llm.temperature is None
    assert config.llm.top_p is None
    assert config.llm.reasoning_effort == "low"
    assert config.seed == 37
    assert config.llm.path == "mock/model"
    assert config.llm.backend == "reps-experiment.ChatClient"
    assert not any(e["type"] == "artifact" for e in events)
    assert not (tmp_path / "artifacts").exists()
    states = [e["data"] for e in events if e["type"] == "custom" and e["kind"] == "govsim.state"]
    assert len(states) == 1
    assert states[0]["round"] == 0
    assert states[0]["resource"] == metrics["final_resource"]
    source_rows = json.loads((tmp_path / "govsim_storage" / experiment / "log_env.json").read_text())
    assert [e["data"] for e in events if e.get("kind") in {"govsim.record", "govsim.harvest", "govsim.utterance", "govsim.summary", "govsim.resource_limit"}] == source_rows
    assert set(metrics) == {
        "rounds", "collapsed", "survival_months", "total_harvest", "gain_per_agent",
        "final_resource", "equality", "over_usage",
    }
    assert_conformant(events)
    typed = [TypeAdapter(Payload).validate_python(event) for event in events]
    customs = [event for event in typed if event.type == "custom"]
    assert customs and all(isinstance(event.data, BaseModel) for event in customs)
    # Preserve the former end-to-end fixture's hint contract. Native rows in
    # no-language treatments intentionally omit optional labels such as agent_name.
    if experiment == "fish_baseline_concurrent":
        for event in customs:
            for path in hint_paths(event.render):
                value = event.model_dump(mode="json")
                for part in path.split("."):
                    assert part in value, (event.kind, path)
                    value = value[part]
    assert next(event.data.embedder for event in customs if event.kind == "govsim.config") == "hash"
    if experiment == "fish_baseline_concurrent":
        assert {event.kind for event in customs} == {
            "govsim.config", "govsim.state", "govsim.upstream_log", "govsim.memory",
            "govsim.harvest", "govsim.utterance", "govsim.summary", "govsim.resource_limit",
        }
        assert {call["agent"] for call in calls} == {
            *(f"persona_{i}" for i in range(5)),
            "framework/summarize_conversation", "framework/find_harvesting_limit",
        }
    assert not any(event.type == "run.status" for event in typed)
    # The receiver fixture includes model defaults. Serialize as emit() does;
    # optional fields disappear, while explicit JSON nulls in native rows stay.
    for event in typed:
        wire = event.model_dump(mode="json", exclude_none=True)
        assert TypeAdapter(Payload).validate_python(wire) == event
        if event.type == "llm.call":
            _assert_no_null(wire)
    storage = tmp_path / "govsim_storage" / experiment
    personas = sorted(path for path in storage.glob("persona_*") if path.is_dir())
    assert personas and all((path / "nodes.json").is_file() for path in personas)
    markers = [(index, event.data) for index, event in enumerate(typed)
               if event.type == "custom" and event.kind == "govsim.upstream_log"]
    assert [data.source for _, data in markers] == [
        "log_env.json", *(f"{path.name}/nodes.json" for path in personas),
    ]
    for index, data in markers:
        raw = (storage / data.source).read_bytes()
        rows = json.loads(raw)
        assert data.bytes == len(raw)
        assert data.sha256 == hashlib.sha256(raw).hexdigest()
        assert data.records == len(rows)
        following = typed[index + 1:index + 1 + data.records]
        if data.source == "log_env.json":
            assert [event.data.root for event in following] == rows
        else:
            persona = Path(data.source).parent.name
            assert all(event.kind == "govsim.memory" and event.data.persona == persona
                       for event in following)
            assert [event.data.node.root for event in following] == rows



@pytest.mark.parametrize("fail_simulation", [True, False])
def test_failed_run_retains_config_and_raw_log(tmp_path, event_capture, fail_simulation, warmed_run):
    config = tmp_path / "params.json"
    config.write_text(json.dumps(params()))
    # A broken checkpoint remains inspectable in the transcript, without artifacts.
    script = '''
import importlib
import os
import sys
from pathlib import Path
from govsim_adapter.main import main

def simulate(cfg, logger, wrappers, wrapper, embedder, storage):
    Path(storage, "log_env.json").write_text("unfinished checkpoint")
    persona = Path(storage, "persona_0")
    persona.mkdir()
    (persona / "nodes.json").write_text('[{"id":1,"description":"completed memory checkpoint"}]')
    if FAIL_SIMULATION:
        raise RuntimeError("simulation failed after checkpoint")

importlib.import_module("simulation.scenarios.fishing.run").run = simulate
raise SystemExit(main())
'''.replace("FAIL_SIMULATION", repr(fail_simulation))
    proc = warmed_run(
        script, config, cwd=tmp_path,
        env=dict(os.environ, REPS_RUN_DIR=str(tmp_path), HF_HUB_OFFLINE="1",
                 TRANSFORMERS_OFFLINE="1"),
    )
    assert proc.returncode == 1, proc.stderr
    events = event_capture.read()
    assert any(e.get("kind") == "govsim.config" for e in events), proc.stderr
    assert next(e["data"]["text"] for e in events if e.get("kind") == "govsim.unparsed_log") == "unfinished checkpoint"
    assert [e["data"]["source"] for e in events if e.get("kind") == "govsim.upstream_log"] == ["log_env.json", "persona_0/nodes.json"]
    assert next(e["data"] for e in events if e.get("kind") == "govsim.memory") == {
        "persona": "persona_0", "node": {"id": 1, "description": "completed memory checkpoint"},
    }
    assert not any(e["type"] == "artifact" for e in events)
    assert not any(e["type"] == "result" for e in events)
    if fail_simulation:
        assert "simulation failed after checkpoint" in proc.stderr


def _reply(text):
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
        }
    )


def test_ingested_discussion_keeps_row_rounds_without_boundary_events(event_capture, tmp_path):
    from reps_events import CustomEvent, parse_event
    from govsim_adapter.upstream import ingest_storage

    rows = [dict(action="utterance", round=round_, agent_id="a", utterance=text)
            for round_ in (0, 3) for text in ("Take two.", "Leave enough for tomorrow.")]
    rows.insert(0, {"action": "harvesting", "round": 0, "wanted_resource": 7,
                    "html_interactions": ["<b>original</b>"], "future_field": {"x": None}})
    rows.append({"action": "future_action", "round": 3, "value": 1.25})
    (tmp_path / "log_env.json").write_text(json.dumps(rows))
    assert ingest_storage(tmp_path) == rows
    events = event_capture.read()
    assert all(isinstance(parse_event(json.dumps(e)), CustomEvent) for e in events)
    assert [e["data"] for e in events if e["kind"] in {"govsim.record", "govsim.harvest", "govsim.utterance", "govsim.summary", "govsim.resource_limit"}] == rows
    assert events[0]["kind"] == "govsim.upstream_log"
    assert events[0]["data"]["source"] == "log_env.json"
    assert events[0]["data"]["records"] == len(rows)
    assert len(events) == len(rows) + 1


def _run_mock_pipeline(tmp_path, warmed_run, *, setup="", **overrides):
    config = tmp_path / "params.json"
    config.write_text(json.dumps(params(**overrides)))
    script = setup + "\nfrom govsim_adapter.main import main; raise SystemExit(main())"
    return warmed_run(
        script, config, cwd=tmp_path,
        env=dict(os.environ, REPS_RUN_DIR=str(tmp_path), REPS_SEED=str(2**31 + 37),
                 HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"),
    )


def _assert_no_null(value):
    assert value is not None
    if isinstance(value, dict):
        for child in value.values():
            _assert_no_null(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_null(child)


def test_resolved_config_copies_no_secrets(tmp_path, monkeypatch, event_capture, warmed_run):
    # Replace credential-shaped host variables too, so the test never forwards
    # real keys and short shell settings cannot accidentally match ordinary data.
    credentials = {
        name: f"fixture-env-{name}-" + "h" * 40 for name in os.environ
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.IGNORECASE)
    } | {
        key: value for name, provider in PROVIDERS.items() for key, value in (
            (provider.api_key.name, f"sk-{name}-" + "a" * 40),
            (provider.base_url.name, f"https://fixture-user:fixture-password@{name}.fixture.invalid/private-endpoint?token=hidden"),
        )
    } | {
        "WANDB_API_KEY": "wandb-fixture-" + "b" * 40,
        "HF_TOKEN": "hf_" + "c" * 40,
        "REPS_TEST_SECRET": "fixture-secret-" + "d" * 40,
        "REPS_TEST_PASSWORD": "fixture-password-" + "e" * 40,
        "REPS_TEST_CREDENTIAL": "fixture-credential-" + "f" * 40,
        "REPS_TEST_AUTH_TOKEN": "Bearer fixture-auth-" + "g" * 40,
    }
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)
    # Assert in the child without writing the sentinel values to the workspace.
    setup = "import os\n" + f"assert all(os.environ.get(k) == v for k, v in {credentials!r}.items())\n"
    proc = _run_mock_pipeline(tmp_path, warmed_run, setup=setup)
    assert proc.returncode == 0, proc.stderr
    events = event_capture.read()
    assert any(event.get("kind") == "govsim.config" for event in events)
    assert any(event["type"] == "llm.call" for event in events)
    (tmp_path / "events.jsonl").write_text("".join(
        TypeAdapter(Payload).validate_python(event).model_dump_json(exclude_none=True) + "\n"
        for event in events
    ))
    (tmp_path / "stdout.txt").write_text(proc.stdout)
    (tmp_path / "stderr.txt").write_text(proc.stderr)
    assert_run_has_no_secrets(tmp_path)


def test_served_model_mismatch_fails_real_upstream_instead_of_using_default_answers(tmp_path, event_capture, warmed_run):
    # Replace only the network. The real upstream wrapper normally catches model
    # exceptions and supplies default answers; a routing error must stop this run.
    setup = '''
from reps_experiment.llm import ChatClient
from openai.types.chat import ChatCompletion
def wrong_model(self, kw):
    self.is_mock = False
    self._request = lambda kw: ChatCompletion.model_validate({
        "id": "mismatch", "object": "chat.completion", "created": 0,
        "model": "wrong-model", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "5"}}],
    })
    return self._create(**kw)
ChatClient._mock_create = wrong_model
'''
    proc = _run_mock_pipeline(tmp_path, warmed_run, setup=setup)
    assert proc.returncode != 0
    events = event_capture.read()
    assert not any(event["type"] == "result" and event["name"] == "status" for event in events)
    calls = [event for event in events if event["type"] == "llm.call"]
    assert len(calls) == 1
    call = calls[0]
    assert call["output"]["model"] == "wrong-model"
    assert call["call"]["response"]["model"] == "wrong-model"
    assert events[events.index(call) + 1]["type"] == "log"
    assert events[events.index(call) + 1]["level"] == "error"
    assert "mock/model" in events[events.index(call) + 1]["message"]
    assert "wrong-model" in events[events.index(call) + 1]["message"]
