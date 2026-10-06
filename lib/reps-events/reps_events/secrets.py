"""Credential leak checks shared by the verifier and experiment test helpers."""

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re


# Pytest must not display helper arguments containing the leaked text itself.
__tracebackhide__ = True

_CREDENTIAL_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
type _Json = str | int | float | bool | None | list[_Json] | dict[str, _Json]
_PATTERNS = (
    ("API key", re.compile(
        r"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_-]{20,}"
        r"|AIza[A-Za-z0-9_-]{35}|AKIA[A-Z0-9]{16}"
        r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|hf_[A-Za-z0-9]{30,})"
    )),
    # A bearer token is >=20 token characters; "bearer of ..." is prose.
    ("Bearer credential", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE)),
    ("URL userinfo", re.compile(r'(?:[A-Za-z][A-Za-z0-9+.-]*:)?//[^/?#\s<>"\\]*@')),
)


def assert_run_has_no_secrets(
    run_dir: str | Path, *, environment: Mapping[str, str] | None = None,
) -> None:
    """Scan every saved file and decoded JSON record for credential material.

    By default, credential values come from the current process environment. Pass
    a snapshot when the run used a different environment. Empty values carry no
    secret and are ignored. Decode all files as UTF-8 with replacement, including
    binary artifacts, so ASCII credentials are checked regardless of file type.
    Failure messages identify the file and category, never the matched value.
    This assertion detects leaks; it does not redact or rewrite evidence.
    """
    root = Path(run_dir)
    if not root.is_dir():
        raise AssertionError("secrets scan requires an existing run directory")
    credentials = {
        name: value for name, value in
        (os.environ if environment is None else environment).items()
        if value and _CREDENTIAL_NAME.search(name)
    }

    def check(text: str, location: str) -> None:
        for name, value in credentials.items():
            if value in text:
                raise AssertionError(f"{location}: environment credential ({name})")
        for category, pattern in _PATTERNS:
            if pattern.search(text):
                raise AssertionError(f"{location}: {category}")

    def check_record(record: _Json, location: str) -> None:
        if isinstance(record, str):
            check(record, location)
        elif isinstance(record, dict):
            for key, value in record.items():
                check(key, location)
                check_record(value, location)
        elif isinstance(record, list):
            for value in record:
                check_record(value, location)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        location = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8", errors="replace")
        check(text, location)
        # Check the decoded values too: JSON escaping must not hide a credential.
        if path.suffix in {".json", ".jsonl"}:
            records = text.splitlines() if path.suffix == ".jsonl" else [text]
            for index, record in enumerate(records, start=1):
                try:
                    decoded: _Json = json.loads(record)
                except ValueError:
                    # Non-JSON artifacts and partial records were still scanned
                    # as text. Wire validity belongs to read_events, not this check.
                    continue
                check_record(decoded, f"{location}:{index}")
