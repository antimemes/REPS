"""Manifest loading, defaults merge, and validation.

Two-phase validation: a spec check before hashing (unknown params, missing required
values, type mismatches), then a check on realized params before launch, where
instantiation checks (minLen, …) also run. In the MVP spec and realized params are
identical (there are no distributions yet); the phases are kept because their error
surfaces differ — spec errors are usage errors (exit 2), realized errors fail the
run. Types describe shape; everything pydantic-ish happens here at instantiation.
"""


import json
import re
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from reps_events import Json

# ---------------------------------------------------------------------------------
# The manifest vocabulary, in ONE place — the conformance sweep checks these
# types against every real nix-built manifest, so drift fails tests.
# TypedDicts, not loose dicts: the manifest is a GENERATED document (mkExperiment),
# so the win worth buying is key-correctness — a typo'd key is a type error, not a
# silent None. Param VALUES stay `Json` (the recursive union from reps_events):
# they are author/user data, validated at runtime by this module.
# ---------------------------------------------------------------------------------


class ParamType(TypedDict):
    """A type descriptor: llm|str|int|float|bool|enum|list|struct|object."""
    kind: str
    values: NotRequired[list[str]]                 # enum
    of: NotRequired["ParamType"]                   # list
    fields: NotRequired[dict[str, "StructField"]]  # struct


class WrappedField(TypedDict):
    """A struct field with presentation hints attached (concordia's per-agent
    model with suggestions); the bare alternative is a ParamType directly."""
    type: ParamType
    suggestions: NotRequired[list["Suggestion"]]
    description: NotRequired[str]


type StructField = ParamType | WrappedField


class SuggestionEntry(TypedDict):
    value: str
    description: NotRequired[str]


type Suggestion = str | SuggestionEntry


class ParamDecl(TypedDict):
    type: ParamType
    initial: NotRequired[Json]
    description: NotRequired[str]
    nullable: NotRequired[bool]
    # presentation order (lower first, default 100) and section label
    order: NotRequired[int]
    group: NotRequired[str]
    suggestions: NotRequired[list[Suggestion]]
    # list instantiation bounds, enforced on realized params (validate_realized)
    minLen: NotRequired[int]
    maxLen: NotRequired[int]
    # fixed typed sub-form (inspect's generate_args), or a variant sub-form keyed
    # by another param's value — currently produced by no in-tree manifest
    fields: NotRequired[dict[str, "ParamDecl"]]
    depends_on: NotRequired[str]
    variants: NotRequired[dict[str, dict[str, "ParamDecl"]]]


class ResultDecl(TypedDict):
    """An ordered, named output declaration, not a runtime value constraint."""
    name: str
    type: ParamType
    label: NotRequired[str]
    description: NotRequired[str]
    details: NotRequired[str]
    unit: NotRequired[str]


class ExtLink(TypedDict):
    label: str
    url: str


class EventSchema(TypedDict):
    """The experiment's payload union, independent of the manifest format."""

    version: int
    models: str
    path: NotRequired[str]


class Manifest(TypedDict):
    name: str
    params: dict[str, ParamDecl]
    v: NotRequired[int]  # older manifests omit this and declare vocabulary 0
    schema_version: NotRequired[int]
    schema: NotRequired[EventSchema]
    summary: NotRequired[str]
    readme: NotRequired[str]  # package-directory README Markdown; presentation only
    origin: NotRequired[str]
    results: NotRequired[list[ResultDecl]]
    env: NotRequired[dict[str, Json]]
    links: NotRequired[list[ExtLink]]


# realized/spec params: user data, runtime-validated against the declarations
type Params = dict[str, Json]


class SchemaError(ValueError):
    pass


def load_manifest(path: str | Path) -> Manifest:
    manifest = json.loads(Path(path).read_text())
    version = manifest.get("v", 0)
    if type(version) is not int or version < 0:
        raise SchemaError("v requires a non-negative integer vocabulary version")
    for field in ("name", "params"):
        if field not in manifest:
            raise SchemaError(f"manifest missing {field!r}")
    if "schema" in manifest:
        schema: dict[str, Any] = manifest["schema"]
        if (not isinstance(manifest["schema"], dict) or not {"version", "models"} <= set(schema)
                or set(schema) - {"version", "models", "path"}
                or ("path" in schema and (not isinstance(schema["path"], str) or not schema["path"]))
                or type(schema.get("version")) is not int or schema["version"] < 0
                or not isinstance(schema.get("models"), str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*", schema["models"]) is None):
            raise SchemaError("schema requires a non-negative integer version and module:attr models pointer")
    return manifest


class MissingParamsError(SchemaError):
    def __init__(self, missing: list[str]):
        super().__init__(f"every param must be bound explicitly; missing {missing}")
        self.missing = missing


def bind_params(manifest: Manifest, overrides: Params) -> Params:
    """Every param must be bound by the invocation — there are NO experiment-level
    defaults. A manifest's `initial` values are suggestions only (including the
    suggested oneliner the CLI prints on this error): they never
    silently enter a run, so an author changing one can never change what an
    existing oneliner means."""
    params_schema = manifest["params"]
    unknown = set(overrides) - set(params_schema)
    if unknown:
        raise SchemaError(f"unknown params {sorted(unknown)}; known: {sorted(params_schema)}")
    missing = sorted(set(params_schema) - set(overrides))
    if missing:
        raise MissingParamsError(missing)
    return {name: overrides[name] for name in params_schema}


def _type_error(path: str, tdesc: ParamType, value: Json) -> SchemaError:
    return SchemaError(f"{path}: expected {tdesc.get('kind')}, got {value!r}")


def field_type(fdesc: StructField) -> ParamType:
    """A struct field is a bare type descriptor ({"kind": ...}), or a param-wrapped one
    ({"type": {...}, "suggestions": [...], ...}) when the author attached presentation
    hints. Validation only cares about the type. A non-mapping descriptor is a
    malformed manifest — raised here, where the malformation is known, not deferred
    (the TypedDict is a static claim about a loaded document; this is its runtime
    check, hence the allowlisted always-true-to-the-checker isinstance)."""
    if not isinstance(fdesc, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise SchemaError(f"field descriptor must be a mapping, got {fdesc!r}")
    if "kind" in fdesc:
        return fdesc
    return fdesc["type"]


def validate_value(value: Json, tdesc: ParamType, path: str) -> None:
    """Strict validation of a fully-concrete value against a type descriptor."""
    kind = tdesc["kind"]
    if kind in ("llm", "str"):
        if not isinstance(value, str):
            raise _type_error(path, tdesc, value)
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise _type_error(path, tdesc, value)
    elif kind == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise _type_error(path, tdesc, value)
    elif kind == "bool":
        if not isinstance(value, bool):
            raise _type_error(path, tdesc, value)
    elif kind == "enum":
        values = tdesc.get("values")
        if values is None:
            raise SchemaError(f"{path}: enum descriptor missing 'values'")
        if value not in values:
            raise SchemaError(f"{path}: {value!r} not in enum {values}")
    elif kind == "list":
        if not isinstance(value, list):
            raise _type_error(path, tdesc, value)
        of = tdesc.get("of")
        if of is None:
            raise SchemaError(f"{path}: list descriptor missing 'of'")
        for i, item in enumerate(value):
            validate_value(item, of, f"{path}[{i}]")
    elif kind == "struct":
        if not isinstance(value, dict):
            raise _type_error(path, tdesc, value)
        fields = tdesc.get("fields")
        if fields is None:
            raise SchemaError(f"{path}: struct descriptor missing 'fields'")
        if set(value) != set(fields):
            raise SchemaError(
                f"{path}: struct fields {sorted(value)} != schema fields {sorted(fields)}"
            )
        for fname, ftype in fields.items():
            validate_value(value[fname], field_type(ftype), f"{path}.{fname}")
    elif kind == "object":
        # a free-form JSON object — arbitrary keys/values (e.g. a wrapped tool's
        # -T/generate args). Materialized as written into the condition hash.
        if not isinstance(value, dict):
            raise _type_error(path, tdesc, value)
    else:
        raise SchemaError(f"{path}: unknown type kind {kind!r}")


def validate_spec(params: Params, manifest: Manifest) -> None:
    for name, value in params.items():
        pschema = manifest["params"][name]
        if value is None and pschema.get("nullable"):
            continue  # explicit null on a nullable param — a bound value, not an omission
        validate_value(value, pschema["type"], name)


def validate_realized(params: Params, manifest: Manifest) -> None:
    for name, value in params.items():
        pschema = manifest["params"][name]
        if value is None and pschema.get("nullable"):
            continue
        validate_value(value, pschema["type"], name)
        if isinstance(value, list):
            n = len(value)
            min_len = pschema.get("minLen")
            if min_len is not None and n < min_len:
                raise SchemaError(f"{name}: length {n} < minLen {min_len}")
            max_len = pschema.get("maxLen")
            if max_len is not None and n > max_len:
                raise SchemaError(f"{name}: length {n} > maxLen {max_len}")
