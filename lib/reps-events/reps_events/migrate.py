"""Migrate written JSON to the current vocabulary before model validation."""

import ast
import re
from typing import Any

from pydantic import JsonValue, TypeAdapter

from .chat import chat_completion_output
from .version import VOCABULARY_VERSION


_ERROR_BODY = re.compile(r"^Error code: [0-9]+ - (.*)$", re.DOTALL)
_RESPONSE = TypeAdapter[JsonValue](JsonValue)


class MigrationError(ValueError):
    """The written vocabulary cannot be migrated to the current version."""


def migrate_record(record: dict[str, Any]) -> dict[str, Any]:
    """Update a decoded record in place, one block per RFC 0002 changelog entry."""
    written_v = record.get("v")
    if type(written_v) is not int or written_v < 0:
        raise MigrationError("record v must be a non-negative integer")
    if written_v > VOCABULARY_VERSION:
        raise MigrationError(f"cannot migrate vocabulary v={written_v} to v={VOCABULARY_VERSION}")
    if written_v < 1:
        event: dict[str, Any] | None = record.get("event")
        if isinstance(event, dict) and event.get("type") == "llm.call":
            _migrate_v0_call(event)
    record["v"] = VOCABULARY_VERSION
    return record


def _migrate_v0_call(event: dict[str, Any]) -> None:
    """Migrate applicable legacy errors or report the failed migration step."""
    error = event.get("error")
    output: dict[str, Any] | None = event.get("output")
    call: dict[str, Any] | None = event.get("call")
    if not isinstance(call, dict):
        call = None  # Leave absent or malformed fields for the current model.
    if error is None:
        if isinstance(output, dict) and output.get("choices", []) == []:
            event["error"] = f"Empty choices for model '{event.get('model')}' (version 0 record, not retried)"
        return
    if not isinstance(error, str) or (match := _ERROR_BODY.fullmatch(error)) is None:
        return
    try:
        literal = ast.literal_eval(match[1])
    except (ValueError, SyntaxError, TypeError, RecursionError) as exc:
        raise MigrationError("v0 llm.call error body is not a Python literal") from exc
    try:
        parsed = _RESPONSE.validate_python(literal, strict=True)
    except (ValueError, TypeError, RecursionError) as exc:
        raise MigrationError("v0 llm.call error body is not JSON-compatible") from exc
    body: dict[str, JsonValue] = parsed if isinstance(parsed, dict) else {"body": parsed}
    if call is not None:
        call["response"] = body
        call.pop("error", None)
    if "choices" in body:
        try:
            converted = chat_completion_output(body)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise MigrationError("cannot convert v0 llm.call error body choices to output") from exc
        else:
            event["output"] = converted.model_dump(mode="json")
            if converted.choices:
                event["error"] = None
                if call is not None:
                    call["error"] = False
