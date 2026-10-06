"""reps-emit: the event vocabulary made executable (docs/book/src/reference/events.md).

    reps-emit custom --kind govsim.record --data '{"action":"utterance","agent_id":"agent-2","utterance":"hi"}'
    reps-emit result --name winner --value village
    reps-emit llm-call --agent a --model mock/x < call.json
    reps-emit schema [TYPE]

Validates against reps_events and submits one event to REPS_EVENT_SOCKET.
The runner acknowledges successful recording. Invalid events or failed delivery
return exit 2. Schema commands print JSON Schema without requiring a runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from pydantic import ValidationError

from reps_events import (
    EVENT_ADAPTER,
    EVENT_MODELS,
    PRODUCER_ADAPTER,
    Envelope,
    EventTransportError,
    Json,
    emit,
    json_schemas,
)

# CLI surface per type: (flag-name, field-name, kind) where kind ∈
# str | json (value parsed as JSON) | jsonish (JSON if it parses, else raw string)
# Required-ness is the model's job, not argparse's.
FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "status": [("--detail", "detail", "str")],
    "log": [("--message", "message", "str"), ("--level", "level", "str")],
    "result": [
        ("--name", "name", "str"),
        ("--value", "value", "jsonish"),
    ],
    "llm-call": [
        ("--agent", "agent", "str"),
        ("--model", "model", "str"),
        ("--input", "input", "json"),
        ("--output", "output", "json"),
        ("--params", "params", "json"),
        ("--tools", "tools", "json"),
        ("--tool-choice", "tool_choice", "jsonish"),
        ("--call", "call", "json"),
        ("--working-time", "working_time", "json"),
        ("--error", "error", "str"),
        ("--retries", "retries", "json"),
    ],
    "custom": [("--kind", "kind", "str"), ("--data", "data", "json")],
}

# subcommand name → wire type name
WIRE_TYPE = {name: name for name in FIELDS}
WIRE_TYPE.update({
    "llm-call": "llm.call",
})


def _parse(kind: str, raw: str, flag: str):
    if kind == "str":
        return raw
    if kind == "jsonish":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    try:  # kind == "json"
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"reps-emit: {flag} expects JSON, got {raw!r} ({exc})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reps-emit", description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name, fields in FIELDS.items():
        sub = subs.add_parser(name, help=f"emit a {WIRE_TYPE[name]} event")
        for flag, _field, _kind in fields:
            sub.add_argument(
                flag, dest=flag.lstrip("-").replace("-", "_"), default=None
            )
    schema = subs.add_parser(
        "schema", help="print JSON Schema for one or all event types"
    )
    schema.add_argument("type", nargs="?", choices=sorted(EVENT_MODELS))
    formats = schema.add_mutually_exclusive_group()
    formats.add_argument(
        "--union", action="store_true", help="complete payload union schema"
    )
    formats.add_argument(
        "--envelope", action="store_true", help="saved JSONL record schema"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "schema":
        if args.type and (args.union or args.envelope):
            raise SystemExit("reps-emit: choose a type or --union/--envelope")
        if args.union or args.envelope:
            schema = (
                EVENT_ADAPTER.json_schema()
                if args.union
                else Envelope.model_json_schema()
            )
            print(json.dumps(schema, indent=2))
            return 0
        schemas = json_schemas()
        if args.type:
            print(json.dumps(schemas[args.type], indent=2))
        else:
            print(json.dumps({t: schemas[t] for t in sorted(schemas)}, indent=2))
        return 0

    wire_type = WIRE_TYPE[args.command]
    body: dict[str, Any] = {}
    for flag, field, kind in FIELDS[args.command]:
        raw = getattr(args, flag.lstrip("-").replace("-", "_"))
        if raw is not None:
            body[field] = _parse(kind, raw, flag)

    # llm-call: the bulky parts (input/output/tools/call) may arrive as one JSON
    # object on stdin instead of flags — flags win field-by-field if both are given.
    if args.command == "llm-call" and not sys.stdin.isatty():
        stdin_raw = sys.stdin.read().strip()
        if stdin_raw:
            stdin_body: Json = _parse("json", stdin_raw, "stdin")
            if not isinstance(stdin_body, dict):
                raise SystemExit("reps-emit: llm-call stdin must be a JSON object")
            body = {**stdin_body, **body}

    try:
        model = PRODUCER_ADAPTER.validate_python(
            {**body, "type": wire_type}, strict=True
        )
    except ValidationError as exc:
        print(f"reps-emit: invalid {wire_type} event: {exc}", file=sys.stderr)
        return 2

    try:
        emit(model)
    except EventTransportError as exc:
        print(f"reps-emit: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
