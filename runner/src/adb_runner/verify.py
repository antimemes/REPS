"""Read-only audit of saved evidence and its rebuildable index card."""

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from adb_events import Envelope, Json, read_events
from adb_events.identity import RUN_ID_PATTERN
from adb_providers import PROVIDERS, served_model_matches

from . import credentials
from .card import derive_card
from .schema import Manifest, load_manifest
from adb_events.secrets import assert_run_has_no_secrets
from .store import find_run, resolve_data_dir


class VerificationError(ValueError):
    """An audit finding, safe to print without exposing record or credential values."""


@dataclass(frozen=True)
class Verification:
    records: int
    credential_values: int
    model_mismatches: tuple[tuple[str, str], ...]
    max_tokens_stops: int
    content_filter_stops: int
    empty_responses: int
    producer_warnings: tuple[str, ...]


# Invoke only the current build's interpreter and declared union, never code paths
# supplied by run.json or event payloads. Capture diagnostics: Pydantic's normal
# error output includes input values and must not echo a leaked credential.
_VALIDATE_UNION = '''
import importlib, sys
from adb_events import read_events
try:
    module, attr = sys.argv[1].split(":")
    payload = getattr(importlib.import_module(module), attr)
except Exception:
    print("cannot import experiment payload union")
    raise SystemExit(1)
count = 0
try:
    for record in read_events(sys.argv[2], payload=payload):
        count += 1
except Exception:
    print(f"experiment payload validation failed at events.jsonl:{count + 1}")
    raise SystemExit(1)
'''


def credential_snapshot(endpoints: Mapping[str, str], environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Check all profiles for recorded endpoints, plus ambient credentials.

    These shell/UI settings have credential-like names but are not secrets and
    are not forwarded to experiments. In particular KEYTIMEOUT is a small integer.
    """
    public_settings = {"KEYTIMEOUT", "LESSKEYIN_SYSTEM", "KITTY_PUBLIC_KEY"}
    values = {key: value for key, value in (os.environ if environment is None else environment).items()
              if key not in public_settings and re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", key, re.I)}
    for provider, profiles in credentials.load().items():
        if provider not in endpoints:
            continue
        template = PROVIDERS.get(provider)
        for profile, fields in profiles.items():
            base_url = fields.get(template.base_url.name if template else f"{provider.upper()}_BASE_URL")
            base_url = base_url or (template.base_url.default if template else "")
            recorded, configured = urlsplit(endpoints[provider]), urlsplit(base_url)
            if (recorded.scheme, recorded.hostname, recorded.port) != (configured.scheme, configured.hostname, configured.port):
                continue
            for name, value in fields.items():
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I):
                    values[f"{provider}.{profile}.{name}"] = value
    return values


def _manifest_path(experiment: str, *, manifest: Path | None, catalog: Path | None) -> Path:
    if manifest is not None:
        return manifest
    if os.environ.get("ADB_MANIFEST"):
        return Path(os.environ["ADB_MANIFEST"])
    if catalog is None and os.environ.get("ADB_MANIFESTS"):
        catalog = Path(os.environ["ADB_MANIFESTS"])
    if catalog is None:
        if not Path("default.nix").is_file():
            raise VerificationError("run verify from an ADB checkout, through the experiment app, or pass --manifest/--catalog")
        built = subprocess.run(["nix-build", "--no-out-link", "-A", "manifests"],
                               text=True, capture_output=True)
        if built.returncode:
            raise VerificationError("could not build the manifest catalog; pass --manifest/--catalog from a successful build")
        catalog = Path(built.stdout.strip())
    # Names come from records; they are never treated as paths or Nix expressions.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", experiment):
        raise VerificationError("invalid experiment identifier")
    return catalog / f"{experiment}.json"


def _records(run_dir: Path) -> list[Envelope[Any]]:
    records: list[Envelope[Any]] = []
    try:
        for record in read_events(run_dir):
            records.append(record)
    except (OSError, ValueError):
        raise VerificationError(f"envelope validation failed at events.jsonl:{len(records) + 1}") from None
    if not records or records[0].event.type != "run.start" or records[-1].event.type != "run.end":
        raise VerificationError("run must start with run.start and finish with run.end")
    first = records[0]
    for seq, record in enumerate(records):
        if record.seq != seq:
            raise VerificationError(f"non-contiguous sequence at events.jsonl:{seq + 1}")
        if (record.run, record.experiment, record.schema_) != (first.run, first.experiment, first.schema_):
            raise VerificationError(f"envelope identity differs at events.jsonl:{seq + 1}")
        if 0 < seq < len(records) - 1 and record.event.type in {"run.start", "run.end"}:
            raise VerificationError(f"unexpected lifecycle record at events.jsonl:{seq + 1}")
    return records


def _validate_union(run_dir: Path, manifest: Manifest, records: list[Envelope[Any]]) -> None:
    schema = manifest.get("schema")
    if not schema or manifest["name"] != records[0].experiment or schema["version"] != records[0].schema_:
        raise VerificationError("manifest experiment/schema does not match the stream")
    interpreter = (Path(schema["path"]).parent / "python") if "path" in schema else Path(sys.executable)
    if not interpreter.is_file():
        raise VerificationError("schema interpreter is unavailable; rebuild the experiment manifest")
    result = subprocess.run([str(interpreter), "-c", _VALIDATE_UNION, schema["models"], str(run_dir.resolve())],
                            text=True, capture_output=True)
    if result.returncode:
        message = result.stdout.strip()
        if not re.fullmatch(r"cannot import experiment payload union|experiment payload validation failed at events.jsonl:\d+", message):
            message = "experiment payload validation failed"
        raise VerificationError(message)


_STORE_ROOT = r"/nix/store/[a-z0-9]{32}-[^/\s\"']+"


def _store_root(path: str | None) -> str | None:
    if path and (match := re.match(_STORE_ROOT + r"(?=/|$)", os.path.normpath(path))):
        return match[0]
    return None


def _python_venvs(program: str, seen: set[str] | None = None) -> set[str]:
    """Read Nix wrapper references without executing them or resolving Python symlinks.

    A venv's bin/python can link to the base interpreter; its lexical store root
    is the environment we need to compare. Shell adapters reference that venv's
    console entrypoint. Unavailable or unrecognized launchers remain unaudited.
    """
    root = _store_root(program)
    if root is None:
        return set()
    if (Path(root) / "pyvenv.cfg").is_file():
        return {root}
    seen = set() if seen is None else seen
    if program in seen or len(seen) >= 8:
        return set()
    seen.add(program)
    try:
        with Path(program).open() as file:
            script = file.read(65536)
    except (OSError, UnicodeError):
        return set()
    if not script.startswith("#!"):
        return set()
    roots: set[str] = set()
    for reference in re.findall(_STORE_ROOT + r"/bin/[A-Za-z0-9._+-]+", script):
        roots.update(_python_venvs(reference, seen))
    return roots


def _audit_producer(records: list[Envelope[Any]]) -> tuple[str, ...]:
    producers = [record for record in records if record.event.type == "producer.python"]
    if not producers:
        return ("producer.python absent (older producer); producer audit unavailable",)
    if len(producers) != 1:
        raise VerificationError("producer audit: expected exactly one producer.python record")
    producer = producers[0]
    # Stdio is captured asynchronously, including import-time dependency noise;
    # it does not establish the order of explicit producer submissions.
    if any(record.event.type not in {"run.start", "stdout", "stderr"}
           for record in records[:producer.seq]):
        raise VerificationError("producer audit: producer.python must precede every other producer event")
    executable = _store_root(producer.event.executable)
    program = records[0].event.runtime.experiment_bin
    if executable is not None and program is not None and _store_root(program) is not None:
        roots = _python_venvs(program)
        if not roots:
            return ("producer audit: executable containment skipped; launcher Python venv could not be recovered",)
        if executable not in roots:
            raise VerificationError("producer audit: executable is outside experiment_bin's Python venv")
    return ()


def verify_run(run_dir: Path, *, manifest: Path | None = None, catalog: Path | None = None,
               environment: Mapping[str, str] | None = None) -> Verification:
    """Audit saved files, typed records, identity/order, the card, and declared results.

    Terminal failed/interrupted runs can also be audited, but missing declared
    results are a finding. This checks evidence, not scientific outcomes or
    successful model responses. Never repairs records or the card. Checks all
    local profiles matching the recorded endpoints.
    """
    records = _records(run_dir)
    values = credential_snapshot(records[0].event.runtime.endpoints, environment)
    try:
        assert_run_has_no_secrets(run_dir, environment=values)
    except AssertionError as exc:
        raise VerificationError(f"secrets scan: {exc}") from None
    path = _manifest_path(records[0].experiment, manifest=manifest, catalog=catalog)
    try:
        declaration = load_manifest(path)
    except (OSError, ValueError, TypeError, KeyError):
        raise VerificationError("cannot load experiment manifest") from None
    _validate_union(run_dir, declaration, records)
    try:
        card: Json = json.loads((run_dir / "run.json").read_text())
    except (OSError, ValueError):
        raise VerificationError("run.json is missing or unreadable") from None
    expected = derive_card(records)
    if not isinstance(card, dict) or set(card) != set(expected):
        raise VerificationError("run.json sections differ from the stream projection")
    for section in expected:
        if json.dumps(card[section], sort_keys=True) != json.dumps(expected[section], sort_keys=True):
            raise VerificationError(f"run.json.{section} differs from the stream projection")
    producer_warnings = _audit_producer(records)
    declared_results = {result["name"] for result in declaration.get("results", [])}
    reported_results = set(expected["derived"]["results"])
    if reported_results != declared_results:
        raise VerificationError(
            f"reported results differ from manifest: missing {sorted(declared_results - reported_results)}; "
            f"extra {sorted(reported_results - declared_results)}"
        )
    for record in records:
        event = record.event
        if event.type == "llm.call" and event.call is not None and "seed" in event.call.request:
            seed = event.call.request["seed"]
            if type(seed) is not int or seed != records[0].event.seed:
                raise VerificationError(f"call.request.seed differs from run.start.seed at events.jsonl:{record.seq + 1}")
    calls = [record.event for record in records if record.event.type == "llm.call"]
    mismatches = {(call.model, call.output.model) for call in calls
                  if (call.output.model or call.output.choices)
                  and not served_model_matches(call.model, call.output.model)}
    stops = sum(any(choice.stop_reason == "max_tokens" for choice in call.output.choices) for call in calls)
    filtered = sum(any(choice.stop_reason == "content_filter" for choice in call.output.choices) for call in calls)
    empty = sum(not call.output.choices and not call.error for call in calls)
    return Verification(len(records), len({value for value in values.values() if value}),
                        tuple(sorted(mismatches)), stops, filtered, empty, producer_warnings)


def verify_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="adb-runner verify", description=__doc__)
    parser.add_argument("run_dir", metavar="RUN_ID_OR_DIR",
                        help="run ID in the data directory, or a path to a run directory")
    parser.add_argument("--data-dir", metavar="DIR",
                        help="run data directory (default $ADB_DATA_DIR, then $XDG_DATA_HOME/adb or ~/.local/share/adb)")
    parser.add_argument("--manifest", type=Path, help="built experiment manifest (defaults to ADB_MANIFEST)")
    parser.add_argument("--catalog", type=Path, help="built manifest directory (otherwise ADB_MANIFESTS or current checkout)")
    args = parser.parse_args(argv)
    try:
        home = resolve_data_dir(args.data_dir)
        run_dir = Path(args.run_dir)
        if re.fullmatch(RUN_ID_PATTERN, args.run_dir):
            found = find_run(home, args.run_dir)
            if found is None:
                raise VerificationError(f"run {args.run_dir} not found in {home}")
            run_dir = found
        result = verify_run(run_dir, manifest=args.manifest, catalog=args.catalog)
    except VerificationError as exc:
        print(f"verify: FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Filesystem, credential-store and import failures must also fail closed,
        # without letting a dependency traceback print inputs or credential values.
        print("verify: FAIL: audit could not complete; check the run, manifest and credential store", file=sys.stderr)
        return 1
    for requested, served in result.model_mismatches:
        print(f"verify: WARN: served model mismatch: requested {requested!r}, served {served!r}", file=sys.stderr)
    for warning in result.producer_warnings:
        print(f"verify: WARN: {warning}", file=sys.stderr)
    print(f"verify: max_tokens stops: {result.max_tokens_stops}; "
          f"content_filter stops: {result.content_filter_stops}; "
          f"empty_responses: {result.empty_responses} (llm.call records)")
    state = "FAIL" if result.model_mismatches else "PASS"
    print(f"verify: {state}: {result.records} records; experiment union, secrets scan "
          f"({result.credential_values} known credential values), card match, producer audit, declared results, and request seeds match")
    return int(bool(result.model_mismatches))
