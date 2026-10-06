"""Defaults merge + validation."""

import pytest
import json

from reps_runner.schema import (
    MissingParamsError,
    SchemaError,
    bind_params,
    validate_realized,
    validate_spec,
    validate_value,
    load_manifest,
)

PLAYERS = [{"model": "m", "role": "w"}, {"model": "m", "role": "v"}]


def test_payload_schema_accepts_an_export_path(tmp_path):
    schema = {"version": 0, "models": "test:Payload", "path": "/nix/store/export/schema.json"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"name": "test", "params": {}, "schema": schema}))
    assert load_manifest(path)["schema"] == schema


@pytest.mark.parametrize("schema", [
    {"version": -1, "models": "m:Payload"}, {"version": True, "models": "m:Payload"},
    {"version": 0, "models": "not-a-pointer"}, {"version": 0}, None,
])
def test_invalid_payload_schema_is_rejected(tmp_path, schema):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"name": "t", "params": {}, "schema": schema}))
    with pytest.raises(SchemaError, match="schema"):
        load_manifest(path)

MANIFEST = {
    "name": "t",
    "params": {
        "players": {
            "type": {"kind": "list", "of": {"kind": "struct", "fields": {
                "model": {"kind": "llm"},
                "role": {"kind": "enum", "values": ["w", "v"]},
            }}},
            "minLen": 2,
            "initial": PLAYERS,  # presentation only — never enters a binding
        },
        "rounds": {"type": {"kind": "int"}, "initial": 2},
        "goal": {"type": {"kind": "str"}},  # no initial: placeholder in suggestions
    },
}


def test_every_param_must_be_bound_explicitly():
    bound = bind_params(MANIFEST, {"players": PLAYERS, "rounds": 5, "goal": "g"})
    assert bound == {"players": PLAYERS, "rounds": 5, "goal": "g"}
    # `initial` is NOT a default: omitting a param that has one still errors
    with pytest.raises(MissingParamsError) as exc:
        bind_params(MANIFEST, {"goal": "g"})
    assert exc.value.missing == ["players", "rounds"]


def test_unknown_params_error():
    with pytest.raises(SchemaError, match="unknown"):
        bind_params(MANIFEST, {"players": PLAYERS, "rounds": 2, "goal": "g", "typo": 1})


def test_llm_kind_accepts_free_text():
    validate_value("any-free-text-id", {"kind": "llm"}, "p")
    with pytest.raises(SchemaError):
        validate_value(3, {"kind": "llm"}, "p")


def test_object_kind_is_free_form():
    validate_value({"anything": [1, {"nested": True}]}, {"kind": "object"}, "p")
    with pytest.raises(SchemaError):
        validate_value([1, 2], {"kind": "object"}, "p")


def test_spec_validates_concrete_values():
    validate_spec({"players": [{"model": "a", "role": "w"},
                               {"model": "b", "role": "v"}],
                   "rounds": 1, "goal": "g"}, MANIFEST)
    with pytest.raises(SchemaError):  # enum membership
        validate_spec({"players": [{"model": "a", "role": "nope"}],
                       "rounds": 1, "goal": "g"}, MANIFEST)


def test_realized_is_strict():
    good = {"players": [{"model": "m", "role": "w"}, {"model": "m", "role": "v"}],
            "rounds": 1, "goal": "g"}
    validate_realized(good, MANIFEST)
    with pytest.raises(SchemaError, match="minLen"):
        validate_realized({**good, "players": good["players"][:1]}, MANIFEST)
    with pytest.raises(SchemaError):  # extra struct field
        validate_realized({**good, "players": [
            {"model": "m", "role": "w", "x": 1}, good["players"][1]]}, MANIFEST)
    with pytest.raises(SchemaError):  # bool is not an int
        validate_realized({**good, "rounds": True}, MANIFEST)
    with pytest.raises(SchemaError):  # enum membership
        validate_realized({**good, "players": [
            {"model": "m", "role": "nope"}, good["players"][1]]}, MANIFEST)


def test_nullable_param_accepts_explicit_null():
    manifest = {"name": "t", "params": {
        "subset": {"type": {"kind": "str"}, "nullable": True},
        "plain": {"type": {"kind": "str"}},
    }}
    validate_spec({"subset": None, "plain": "x"}, manifest)
    validate_realized({"subset": None, "plain": "x"}, manifest)
    with pytest.raises(SchemaError):  # null is only valid on nullable params
        validate_spec({"subset": None, "plain": None}, manifest)
    validate_spec({"subset": "mmlu", "plain": "x"}, manifest)  # non-null still typed
    with pytest.raises(SchemaError):
        validate_spec({"subset": 3, "plain": "x"}, manifest)
