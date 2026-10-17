"""CLI value shorthand: JSON, @file, or a bare string.

    VALUE := JSON | @path | bare-string

JSON parses as JSON; `@path` reads the file (JSON if it parses, if not the raw text is
the string value); all other input is a bare string. A value with a JSON start
(`[`/`{` first) that does not parse stops with a clear error rather than becoming a
string with no warning.
"""

from __future__ import annotations

import json
from pathlib import Path


class ShorthandError(ValueError):
    pass


def parse_value(raw: str):
    s = raw.strip()
    if not s:
        raise ShorthandError("empty value")
    if s.startswith("@"):
        text = Path(s[1:]).read_text()
        stripped = text.strip()
        if stripped[:1] in "[{":  # unambiguously intended as JSON — a clear error
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ShorthandError(f"malformed JSON in {s[1:]}: {exc}") from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text  # raw text file — the full file is the (string) value
    if s[0] in "[{":  # unambiguously intended as JSON — a clear error, not a bare string
        try:
            return json.loads(s)
        except json.JSONDecodeError as exc:
            raise ShorthandError(f"malformed JSON value {s[:80]!r}: {exc}") from exc
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s  # bare string
