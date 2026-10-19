"""Publish verified terminal runs through the user's ordinary boto3 configuration.

Uses only HeadObject, GetObject and PutObject.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError
if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
from reps_events import Json
import zstandard

from .store import condition_name, resolve_data_dir
from .verify import VerificationError, verify_run


class PublishError(ValueError):
    """A publishing failure safe to report without credential values."""


SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")
NONE = "NONE"
LICENSES = ("CC-BY-4.0", "CC0-1.0", "CC-BY-SA-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0", NONE)


def check_spdx(value: str) -> str:
    """A valid --license value; any case of NONE becomes NONE, which writes no declaration."""
    if value.upper() == "NOASSERTION":
        raise PublishError("--license NOASSERTION is not accepted; pass --license NONE to publish without a license")
    if not SPDX.fullmatch(value):
        raise PublishError(f"--license must be an SPDX license identifier, not {value!r}")
    return NONE if value.upper() == NONE else value


def license_body(spdx: str) -> bytes:
    return (json.dumps({"v": 0, "spdx": spdx}, separators=(",", ":")) + "\n").encode()


def parse_license(body: bytes) -> str:
    try:
        value: Json = json.loads(body)
    except ValueError:
        value = None
    spdx = value.get("spdx") if isinstance(value, dict) else None
    if not (isinstance(value, dict) and set(value) == {"v", "spdx"} and type(value["v"]) is int
            and value["v"] == 0 and isinstance(spdx, str) and SPDX.fullmatch(spdx)):
        raise PublishError('malformed license declaration; expected {"v":0,"spdx":"<SPDX identifier>"}')
    if spdx.upper() in {NONE, "NOASSERTION"}:
        raise PublishError(f"{spdx} is not a license; license.json must name one")
    return spdx


def ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def prompt_license(where: str) -> str:
    choices = {str(number): spdx for number, spdx in enumerate(LICENSES, 1)}
    other = str(len(choices) + 1)
    print(f"{where} declares no license. Choose the license for runs published there:", file=sys.stderr)
    for number, spdx in choices.items():
        print(f"  {number}) {'No license' if spdx == NONE else spdx}{' (default)' if number == '1' else ''}",
              file=sys.stderr)
    print(f"  {other}) Other: an identifier from https://spdx.org/licenses/<id>.html", file=sys.stderr)
    while True:
        choice = ask(f"license [1-{other} or SPDX id]: ") or "1"
        if choice in choices:
            return choices[choice]
        if choice == other:
            choice = ask("SPDX identifier (https://spdx.org/licenses/<id>.html): ")
        if re.search(r"[A-Za-z]", choice):
            try:
                return check_spdx(choice)
            except PublishError:
                pass
        print(f"enter 1-{other} or an SPDX identifier", file=sys.stderr)


@dataclass(frozen=True)
class Target:
    bucket: str
    prefix: str

    def key(self, suffix: str) -> str:
        return f"{self.prefix}/{suffix}" if self.prefix else suffix


def parse_target(value: str) -> Target:
    url = urlsplit(value)
    if (url.scheme != "s3" or not url.netloc or url.username or url.password or
            url.query or url.fragment or ":" in url.netloc):
        raise PublishError("target must be s3://<bucket>/<prefix>")
    prefix = url.path.strip("/")
    if any(part in {"", ".", ".."} for part in prefix.split("/")) and prefix:
        raise PublishError("target prefix must not contain empty, . or .. components")
    return Target(url.netloc, prefix)


def run_stem(card: dict[str, Any]) -> str:
    identity = card["identity"]
    run: str = identity["run"]
    if not re.fullmatch(r"[0-9]{8}t[0-9]{6}z-[0-9a-f]{12}", run):
        raise PublishError("invalid run ID")
    return f"{condition_name(identity['condition'], identity['experiment'])}/{run}"


def select_runs(home: Path, stems: list[str], experiment: str | None) -> list[Path]:
    paths: set[Path] = set()
    for stem in stems:
        parts = stem.split("/")
        if len(parts) not in {1, 2} or any(not re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts):
            raise PublishError("STEM must be <cid>-<exp> or <cid>-<exp>/<run>")
        path = home / "runs" / stem
        if len(parts) == 2:
            paths.add(path)
        elif path.is_dir():
            paths.update(p for p in path.iterdir() if p.is_dir())
        else:
            raise PublishError(f"no such stem: {stem}")
    if not stems:
        paths.update(p for p in (home / "runs").glob("*/*") if p.is_dir())
    if experiment is not None:
        # Discovery reads identity, does not parse the informational experiment suffix.
        filtered: set[Path] = set()
        for path in paths:
            try:
                card = json.loads((path / "run.json").read_bytes())
                if card["identity"]["experiment"] == experiment:
                    filtered.add(path)
            except (OSError, ValueError, KeyError, TypeError):
                # Keep damaged candidates for the per-run gate to report.
                filtered.add(path)
        paths = filtered
    return sorted(paths)


class Publisher:
    def __init__(self, to: str, profile: str | None = None):
        self.target = parse_target(to)
        self.s3: S3Client = boto3.Session(profile_name=profile).client("s3")

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.target.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def get(self, key: str) -> bytes | None:
        try:
            response = self.s3.get_object(Bucket=self.target.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def resolve_license(self, requested: str | None, interactive: bool = False) -> tuple[str, bool]:
        """The license in effect and whether the target already declares it."""
        key = self.target.key("license.json")
        body = self.get(key)
        if body is None:
            if requested is not None:
                return requested, False
            if interactive:
                return prompt_license(f"s3://{self.target.bucket}/{self.target.prefix}"), False
            raise PublishError("target declares no license; pass --license SPDX")
        try:
            declared = parse_license(body)
        except PublishError as error:
            raise PublishError(f"s3://{self.target.bucket}/{key}: {error}") from None
        if requested == NONE:
            print(f"publish: WARNING: s3://{self.target.bucket}/{key} declares {declared}; publishing under it",
                  file=sys.stderr)
        elif requested is not None and requested != declared:
            raise PublishError(f"s3://{self.target.bucket}/{key} declares {declared}, not {requested}; "
                               f"pass --license {declared} or choose a new prefix")
        return declared, True

    def declare_license(self, requested: str | None, *, interactive: bool = False, preview: bool = False,
                        log: Callable[[str], None] = print) -> str:
        """Write the prefix's license declaration once; an existing one is never replaced. NONE writes none."""
        spdx, declared = self.resolve_license(requested, interactive)
        if not declared and spdx != NONE:
            key, body = self.target.key("license.json"), license_body(spdx)
            log(f"{'PLAN' if preview else 'PUT'} s3://{self.target.bucket}/{key} {len(body)} bytes")
            if not preview:
                self.s3.put_object(Bucket=self.target.bucket, Key=key, Body=body,
                                   ContentType="application/json")
        log(f"LICENSE s3://{self.target.bucket}/{self.target.prefix} {'undeclared' if spdx == NONE else spdx}")
        return spdx

    def upload_run(self, directory: Path, *, manifest: Path | None = None, preview: bool = False,
                   log: Callable[[str], None] = print) -> None:
        card: dict[str, Any] = json.loads((directory / "run.json").read_bytes())
        if card["lifecycle"]["state"] not in {"completed", "failed", "interrupted"}:
            raise PublishError("run is not terminal")
        if not card.get("provenance", {}).get("fetch_ref"):
            raise PublishError("run has no provenance.fetch_ref (pinned clean revision required)")
        audit = verify_run(directory, manifest=manifest)
        if audit.model_mismatches:
            raise PublishError("verify failed: served model mismatch")
        stem = run_stem(card)
        if directory.name != card["identity"]["run"] or directory.parent.name != stem.split("/")[0]:
            raise PublishError("run path differs from card identity")
        base = self.target.key(f"runs/{stem}")
        keys = [f"{base}/events.jsonl.zst", f"{base}/run.json"]
        existing = [self.exists(key) for key in keys]
        if any(existing):
            raise PublishError("refusing to overwrite existing run keys")
        # Compression lives outside the data directory, with one level-19 frame.
        with TemporaryDirectory(prefix="reps-publish-") as temporary:
            compressed = Path(temporary) / "events.jsonl.zst"
            events = directory / "events.jsonl"
            with events.open("rb") as source, compressed.open("wb") as destination:
                zstandard.ZstdCompressor(level=19).copy_stream(source, destination, size=events.stat().st_size)
            body = (directory / "run.json").read_bytes()
            for key, size in zip(keys, [compressed.stat().st_size, len(body)]):
                log(f"{'PLAN' if preview else 'PUT'} s3://{self.target.bucket}/{key} {size} bytes")
            if not preview:
                with compressed.open("rb") as stream:
                    self.s3.put_object(Bucket=self.target.bucket, Key=keys[0], Body=stream,
                                       ContentType="application/zstd")
                # The card commits the run; an interrupted upload stays immutable.
                self.s3.put_object(Bucket=self.target.bucket, Key=keys[1], Body=body,
                                   ContentType="application/json")


def failure(error: Exception) -> str:
    return str(error) if isinstance(error, (PublishError, VerificationError, BotoCoreError, ClientError)) else type(error).__name__


def publish_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="reps-runner publish", description=__doc__)
    parser.add_argument("--to", required=True, metavar="s3://BUCKET/PREFIX")
    parser.add_argument("stems", nargs="*", metavar="STEM")
    parser.add_argument("--experiment")
    parser.add_argument("--profile", metavar="NAME", help="AWS profile (default: boto3's normal resolution)")
    parser.add_argument("--license", metavar="SPDX",
                        help="license declared once for the prefix, or NONE to declare none "
                             "(default: the declared one; prompted on a terminal)")
    parser.add_argument("--data-dir")
    parser.add_argument("--dry-run", action="store_true", help="verify and print exact object keys and sizes; write nothing")
    args = parser.parse_args(argv)
    if args.profile is not None and "=" in args.profile:
        parser.error("AWS --profile NAME cannot contain =; use --credential SET=NAME for credentials")
    if args.license is not None:
        try:
            args.license = check_spdx(args.license)
        except PublishError as error:
            parser.error(str(error))
    try:
        publisher = Publisher(args.to, args.profile)
        paths = select_runs(resolve_data_dir(args.data_dir), args.stems, args.experiment)
        publisher.declare_license(args.license, interactive=sys.stdin.isatty(), preview=args.dry_run)
    except Exception as error:
        print(f"publish: FAIL: {failure(error)}", file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        try:
            publisher.upload_run(path, preview=args.dry_run)
        except Exception as error:
            failed = True
            print(f"publish: FAIL {path.parent.name}/{path.name}: {failure(error)}", file=sys.stderr)
    return int(failed)


def resolve_license(to: str, requested: str | None, *, profile: str | None = None,
                    interactive: bool = False) -> str:
    """The license publish_run will declare, resolved before the run starts."""
    return Publisher(to, profile).resolve_license(requested, interactive)[0]


def publish_run(directory: Path, to: str, *, profile: str | None = None, license: str | None = None,
                manifest: Path | None = None, log: Callable[[str], None] = print) -> None:
    """Publish the verified run from one completed invocation."""
    publisher = Publisher(to, profile)
    publisher.declare_license(license, log=log)
    publisher.upload_run(directory, manifest=manifest, log=log)
