"""Publish verified terminal runs through the user's ordinary boto3 configuration.

Uses only HeadObject and PutObject.
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
import zstandard

from .store import condition_name, resolve_data_dir
from .verify import VerificationError, verify_run


class PublishError(ValueError):
    """A publishing failure safe to report without credential values."""


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
        # Discovery reads identity, never parses the informational experiment suffix.
        filtered: set[Path] = set()
        for path in paths:
            try:
                card = json.loads((path / "run.json").read_bytes())
                if card["identity"]["experiment"] == experiment:
                    filtered.add(path)
            except (OSError, ValueError, KeyError, TypeError):
                # Keep damaged candidates so the per-run gate reports them.
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
                # The card commits the run; an interrupted upload remains immutable.
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
    parser.add_argument("--data-dir")
    parser.add_argument("--dry-run", action="store_true", help="verify and print exact object keys and sizes; write nothing")
    args = parser.parse_args(argv)
    if args.profile is not None and "=" in args.profile:
        parser.error("AWS --profile NAME cannot contain =; use --credential SET=NAME for credentials")
    try:
        publisher = Publisher(args.to, args.profile)
        paths = select_runs(resolve_data_dir(args.data_dir), args.stems, args.experiment)
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


def publish_run(directory: Path, to: str, *, profile: str | None = None,
                manifest: Path | None = None, log: Callable[[str], None] = print) -> None:
    """Publish the verified run from one completed invocation."""
    publisher = Publisher(to, profile)
    publisher.upload_run(directory, manifest=manifest, log=log)
