import json
from importlib.resources import files
from typing import ClassVar, Literal

import pytest
from pydantic import ValidationError

from reps_events import ActorRegistry, CustomEvent, EVENT_MODELS, Payload, RenderHint, export_schema
from reps_events.models.base import Model
from reps_events.render import hint_path_declared, hint_paths, template_paths


def test_render_hints_are_frozen_and_off_the_wire():
    hint = RenderHint(icon="info", body="message", actor_registry=ActorRegistry(path="data.actors", label="name"))
    with pytest.raises(ValidationError):
        hint.icon = "bug"
    with pytest.raises(ValidationError):
        hint.actor_registry.label = "other"
    with pytest.raises(ValidationError):
        RenderHint.model_validate({"icon": "info", "actor_registry": {"path": "actors", "label": "name", "extra": True}})
    for model in EVENT_MODELS.values():
        assert "render" not in model.model_fields
        assert "render_variants" not in model.model_fields


def test_export_round_trips_hints_and_declared_paths():
    class Actor(Model):
        name: str

    class Data(Model):
        actor: str
        name: str
        text: str
        actors: dict[str, Actor]

    class Message(CustomEvent[Data]):
        kind: Literal["test.message"] = "test.message"
        render: ClassVar[RenderHint] = RenderHint(
            icon="message-circle", actor="data.actor", actor_label="data.name",
            title="{data.actor}: {data.name}", body="data.text", format="html-text",
            fields=["data.actor", "data.name"], badge="data.actor",
            actor_registry=ActorRegistry(path="data.actors", label="name"),
        )

    root = json.loads(json.dumps(export_schema(Payload | Message)))
    for model in {*EVENT_MODELS.values(), Message}:
        schema = root["$defs"][model.__name__]
        if getattr(model, "render", None) is None:
            assert "x-reps-render" not in schema
            continue
        assert RenderHint.model_validate(schema["x-reps-render"]) == model.render
        hints = [model.render] + [h for variants in model.render_variants.values() for h in variants.values()]
        for hint in hints:
            for path in hint_paths(hint):
                if path:
                    assert hint_path_declared(schema, path, root), (model.__name__, path)
            if hint.actor_registry:
                assert hint_path_declared(schema, hint.actor_registry.path, root)
                entry = root["$defs"]["Data"]["properties"]["actors"]["additionalProperties"]
                assert hint_path_declared(entry, hint.actor_registry.label, root)
    assert not hint_path_declared(root["$defs"]["Message"], "data.missing", root)


def test_literal_dependent_hints_round_trip():
    root = export_schema(Payload)
    for name in ("Log", "CapturedLine"):
        for clause in root["$defs"][name]["allOf"]:
            field, condition = next(iter(clause["if"]["properties"].items()))
            model = EVENT_MODELS["log" if name == "Log" else "stdout"]
            assert RenderHint.model_validate(clause["then"]["x-reps-render"]) == model.render_variants[field][condition["const"]]


def test_packaged_shared_export_is_current():
    assert json.loads(files("reps_events").joinpath("schema.json").read_text()) == export_schema(Payload)


@pytest.mark.parametrize("template,paths", [
    ("data.name", ["data.name"]),
    ("round {data.round} · pool {data.resource}", ["data.round", "data.resource"]),
    ("literal text", []),
    ("{data.name}: {data.name}", ["data.name", "data.name"]),
])
def test_templates_export_verbatim_and_enumerate_only_paths(template, paths):
    hint = RenderHint(icon="info", title=template)
    assert hint.model_dump()["title"] == template
    assert template_paths(template) == paths


@pytest.mark.parametrize("template", ["{data.count or 0}", "{len(data.name)}", "{data.name!r}", "{data.name:>10}", "{{data.name}}"])
def test_templates_reject_expressions_and_formatting(template):
    with pytest.raises(ValidationError, match="only.*dotted.path"):
        RenderHint(icon="info", body=template)
