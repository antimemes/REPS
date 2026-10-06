#!/usr/bin/env python3
"""Regenerate generate_fields.json — the typed sub-form schema for `generate_args`.

One command, no hand edits:  task genconfig:update  (runs `uv run python
scripts/...` here — the script introspects the PINNED inspect_ai).

`generate_args` stays an object param (a generation override is "absent = the
provider's default", which flat required-bound params cannot express), but the
GUI renders it as a typed form instead of a JSON blob: each field of inspect's
GenerateConfig that is a plain generation knob becomes a form field, typed from
the model's own annotations and described by its attribute docstring.

Excluded by name: transport/reliability infra that is environment, not
condition (retries, timeouts, connections, caching, batching, fallbacks) and
`seed`, which REPS owns. Excluded by shape: fields that aren't scalar/enum/
str-list (response_schema, logit_bias, extra_body, ...) — those remain settable
as raw JSON keys in the object. Deterministic output: a clean `git diff` after
running IS the update.
"""

from __future__ import annotations

import ast
import json
import sys
import types
import typing
from pathlib import Path

EXCLUDE = {
    # transport/reliability/caching/batching: environment, never condition
    "max_retries", "timeout", "attempt_timeout", "max_connections",
    "adaptive_connections", "cache", "cache_prompt", "batch", "extra_headers",
    "fallback_models",
    # REPS owns seeding (the runner passes $REPS_SEED)
    "seed",
}

SCALAR = {str: "str", int: "int", float: "float", bool: "bool"}


def map_type(ann) -> dict | None:
    """annotation -> reps type descriptor, or None when not representable."""
    args = [a for a in typing.get_args(ann) if a is not type(None)]
    if typing.get_origin(ann) in (typing.Union, types.UnionType) and len(args) == 1:
        return map_type(args[0])
    if ann in SCALAR:
        return {"kind": SCALAR[ann]}
    if typing.get_origin(ann) is typing.Literal:
        values = typing.get_args(ann)
        if all(isinstance(v, str) for v in values):
            return {"kind": "enum", "values": list(values)}
    if typing.get_origin(ann) is list:
        of = map_type(typing.get_args(ann)[0])
        if of:
            return {"kind": "list", "of": of}
    return None


def attribute_docs(cls) -> dict[str, str]:
    """Field name -> the attribute docstring below its annotation."""
    import inspect as pyinspect

    tree = ast.parse(pyinspect.getsource(cls))
    body = tree.body[0].body
    docs, prev = {}, None
    for node in body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            prev = node.target.id
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and prev:
            docs[prev] = " ".join(str(node.value.value).split())
            prev = None
        else:
            prev = None
    return docs


def main() -> int:
    from inspect_ai.model import GenerateConfig

    docs = attribute_docs(GenerateConfig)
    fields, skipped = {}, []
    for name, field in GenerateConfig.model_fields.items():
        if name in EXCLUDE:
            continue
        t = map_type(field.annotation)
        if t is None:
            skipped.append(name)
            continue
        fields[name] = {"type": t, "description": docs.get(name, "")}

    out = {
        "_generated_by": "scripts/update_generate_fields.py — do not edit by hand",
        "fields": dict(sorted(fields.items())),
    }
    path = Path(__file__).resolve().parent.parent / "generate_fields.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {path.name}: {len(fields)} fields "
          f"(skipped non-representable: {', '.join(skipped) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
