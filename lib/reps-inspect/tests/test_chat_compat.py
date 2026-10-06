"""Cheap drift check using the Inspect dependency this package already has."""

import json
from pathlib import Path

from inspect_ai.model import ChatMessage, ModelOutput
from pydantic import TypeAdapter


def test_serialized_llm_call_is_inspect_compatible():
    path = Path(__file__).parents[2] / "reps-events/tests/fixtures/llm-call.json"
    payload = json.loads(path.read_text())
    TypeAdapter(list[ChatMessage]).validate_json(json.dumps(payload["input"]))
    TypeAdapter(ModelOutput).validate_json(json.dumps(payload["output"]))
