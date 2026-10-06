"""Compare the installed Inspect declarations with the vendored data snapshot."""

from dataclasses import fields, is_dataclass
from importlib import import_module
from importlib.metadata import version
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin, get_type_hints
import warnings

from packaging.version import Version
from pydantic import BaseModel

from reps_events import inspect_chat


def _type_name(annotation):
    """Compare declared data types, ignoring module paths and local validators."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return _type_name(args[0])
    if origin in (Union, UnionType):
        return " | ".join(sorted(_type_name(arg) for arg in args))
    if origin is Literal:
        return f"Literal[{', '.join(sorted(map(repr, args)))}]"
    if origin is not None:
        return f"{_type_name(origin)}[{', '.join(_type_name(arg) for arg in args)}]"
    return getattr(annotation, "__name__", str(annotation))


def _declared_fields(model):
    if issubclass(model, BaseModel):
        return {name: field.annotation for name, field in model.model_fields.items()}
    assert is_dataclass(model), f"Inspect changed {model.__name__}'s model representation"
    hints = get_type_hints(model)
    return {field.name: hints[field.name] for field in fields(model)}


def test_inspect_data_schema_drift(capsys):
    installed = Version(version("inspect-ai"))
    # Since 0.3.184, input_tokens excludes cache reads/writes. The runner adds
    # those counters to get total input; an older Inspect would double-count.
    assert installed >= Version("0.3.184"), "Inspect input_tokens must exclude cached tokens"
    upstream_modules = [import_module(f"inspect_ai.{module}") for module in (
        "model._chat_message", "model._model_output", "model._model_call",
        "_util.content", "_util.citation", "tool._tool_call",
        "tool._tool_info", "tool._tool_choice",
    )]
    vendored_models = {
        name: model for name, model in vars(inspect_chat).items()
        if isinstance(model, type) and issubclass(model, BaseModel)
        and model.__module__ == inspect_chat.__name__ and not name.startswith("_")
    }
    # Explicit local changes, not additions to the upstream data vocabulary.
    # ToolCallContent and ToolCallView are deliberately not vendored: viewer data.
    omissions = {"ModelCall.call_refs", "ModelCall.call_key", "ToolCall.view"} | {
        f"{name}.source" for name, model in vendored_models.items()
        if issubclass(model, inspect_chat.ChatMessageBase)
    }
    retypes = {
        "ToolInfo.parameters": ("dict[str, Any]", "ToolParams"),
        # Boundary APIs and other harnesses have error types beyond Inspect's runtime.
        "ToolCallError.type": ("str", _type_name(Literal[
            "parsing", "timeout", "unicode_decode", "permission", "file_not_found",
            "is_a_directory", "limit", "approval", "cancelled", "sandbox_unavailable",
            "unknown", "output_limit",
        ])),
    }
    additions, breaking, intentional = [], [], []
    for name, vendored in sorted(vendored_models.items()):
        upstream = next((getattr(m, name) for m in upstream_modules if hasattr(m, name)), None)
        if upstream is None:
            breaking.append(f"- {name}: class missing from Inspect")
            continue
        ours, theirs = _declared_fields(vendored), _declared_fields(upstream)
        for field in sorted(theirs.keys() - ours.keys()):
            path = f"{name}.{field}"
            line = f"+ {path}: {_type_name(theirs[field])}"
            (intentional if path in omissions else additions).append(line)
        for field in sorted(ours.keys() - theirs.keys()):
            breaking.append(f"- {name}.{field}: {_type_name(ours[field])} missing from Inspect")
        for field in sorted(ours.keys() & theirs.keys()):
            path = f"{name}.{field}"
            local_type, upstream_type = _type_name(ours[field]), _type_name(theirs[field])
            if path in retypes and (local_type, upstream_type) == retypes[path]:
                intentional.append(f"~ {path}: vendored {local_type}; Inspect {upstream_type}")
            elif local_type != upstream_type:
                breaking.append(f"~ {path}: vendored {local_type}; Inspect {upstream_type}")

    diff = "\n".join([
        f"Inspect {installed} vs vendored data models ({len(vendored_models)} classes):",
        *additions, *breaking,
        "Intentional local differences:", *intentional,
    ])
    with capsys.disabled():
        print(f"\n{diff}")
    if additions:
        warnings.warn("Inspect fields available to revendor:\n" + "\n".join(additions),
                      UserWarning, stacklevel=1)
    assert not breaking, diff
