"""Build a local index directory from experiment buckets, using only ListObjectsV2 and GetObject."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, TYPE_CHECKING
from urllib.parse import urlsplit

import boto3
import yaml
from botocore.exceptions import ClientError
from adb_events import Json
from adb_events.models.base import serialize_utc_datetime

from .publish import PublishError, Target, failure, parse_target

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


@dataclass(frozen=True)
class Exclusion:
    runs: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Store:
    target: Target
    profile: str | None
    url: str
    filters: dict[str, list[str]]
    exclude: tuple[Exclusion, ...] = ()


def read_stores(path: Path) -> list[Store]:
    raw = path.read_bytes()
    value: Json
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            value = yaml.safe_load(raw)
        except yaml.YAMLError:
            raise PublishError("invalid YAML store list") from None
    else:
        value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get("v")) is not int or value.get("v") != 0:
        raise PublishError("store list must be {v: 0, stores: [...]}")
    entries = value.get("stores")
    if not isinstance(entries, list):
        raise PublishError("store list stores must be an array")
    stores: list[Store] = []
    for row in entries:
        if not isinstance(row, dict):
            raise PublishError("each store must be an object")
        s3, public = row.get("s3"), row.get("url")
        if not isinstance(s3, str) or not isinstance(public, str):
            raise PublishError("each store needs s3 and url strings")
        url = urlsplit(public)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise PublishError("store url must be a public HTTPS base without credentials, query or fragment")
        profile = row.get("profile")
        if profile is not None and (not isinstance(profile, str) or "=" in profile):
            raise PublishError("store profile must be an AWS profile name")
        filters: dict[str, list[str]] = {}
        for key in ("experiments", "conditions", "runs"):
            if key in row:
                values = row[key]
                if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                    raise PublishError(f"store {key} must be a list of strings")
                filters[key] = [item for item in values if isinstance(item, str)]
        excluded = row.get("exclude", [])
        if not isinstance(excluded, list):
            raise PublishError("store exclude must be a list of entries")
        exclusions: list[Exclusion] = []
        seen: set[str] = set()
        for entry in excluded:
            if not isinstance(entry, dict) or set(entry) != {"runs", "reason"}:
                raise PublishError("store exclude entries must be objects with runs and reason")
            runs, reason = entry["runs"], entry["reason"]
            if (not isinstance(runs, list) or not runs
                    or not all(isinstance(run, str) and run.strip() for run in runs)):
                raise PublishError("store exclude runs must be a non-empty list of non-empty strings")
            if not isinstance(reason, str) or not reason.strip():
                raise PublishError("store exclude reason must be a non-empty string")
            run_ids = tuple(run for run in runs if isinstance(run, str))
            for run in run_ids:
                if run in seen:
                    raise PublishError(f"store exclude repeats run {run!r} across entries")
            seen.update(run_ids)
            exclusions.append(Exclusion(run_ids, reason))
        stores.append(Store(parse_target(s3), profile, public.rstrip("/"), filters, tuple(exclusions)))
    return stores


def client(profile: str | None) -> S3Client:
    return boto3.Session(profile_name=profile).client("s3")


def get(s3: S3Client, target: Target, key: str) -> bytes | None:
    try:
        response = s3.get_object(Bucket=target.bucket, Key=key)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def keys(s3: S3Client, target: Target, prefix: str) -> dict[str, str]:
    return {obj["Key"]: obj.get("ETag", "")
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=target.bucket, Prefix=prefix)
            for obj in page.get("Contents", []) if "Key" in obj}


def resolve_cache_dir(directory: Path | None = None) -> Path:
    if directory is not None:
        return directory.resolve()
    xdg = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return (Path(xdg) / "adb" / "index").resolve()


def cache_path(cache: Path | None, store: Store, key: str, etag: str) -> Path | None:
    if cache is None or not etag:
        return None
    profile = "default" if store.profile is None else store.profile
    parts = f"{profile}/{store.target.bucket}/{key}".split("/")
    # Reject ambiguous segments instead of using normpath, which can merge distinct
    # S3 keys into one cache entry. ETags need not be unique across objects, so
    # bypass the cache for these keys.
    if "/" in profile or any(part in {"", ".", ".."} for part in parts):
        return None
    return cache / profile / store.target.bucket / key


def get_card(s3: S3Client, store: Store, key: str, etag: str,
             cache: Path | None, dry_run: bool) -> tuple[bytes | None, bool]:
    path = cache_path(cache, store, key, etag)
    if path is not None:
        try:
            if path.with_name(path.name + ".etag").read_bytes() == etag.encode("utf-8"):
                return path.read_bytes(), True
        except OSError:
            pass
    body = get(s3, store.target, key)
    if path is not None and body is not None and not dry_run:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            token = path.with_name(path.name + ".etag")
            # Invalidate the old pair before replacing the card; publish the token last.
            token.unlink(missing_ok=True)
            path.write_bytes(body)
            token.write_bytes(etag.encode("utf-8"))
        except OSError:
            pass  # Cache availability must not decide which runs are indexed.
    return body, False


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def collect(stores: list[Store], cache: Path | None = None, dry_run: bool = False) -> dict[str, list[bytes]]:
    grouped: dict[str, list[bytes]] = {}
    for store in stores:
        s3 = client(store.profile)
        prefix = store.target.key("runs/")
        objects = keys(s3, store.target, prefix)
        exclusions = {run: exclusion for exclusion in store.exclude for run in exclusion.runs}
        unseen = set(exclusions)
        count = cached = fetched = 0
        for key in sorted(objects):
            if not re.fullmatch(r"[^/]+/[^/]+/run\.json", key[len(prefix):]):
                continue
            if key[:-len("run.json")] + "events.jsonl.zst" not in objects:
                continue
            try:
                body, hit = get_card(s3, store, key, objects[key], cache, dry_run)
                if body is None:
                    raise PublishError("card disappeared while indexing")
                if hit:
                    cached += 1
                else:
                    fetched += 1
                card: Json = json.loads(body)
                if not isinstance(card, dict):
                    raise PublishError("card must be an object with an identity object")
                identity = card.get("identity")
                if not isinstance(identity, dict):
                    raise PublishError("card must be an object with an identity object")
                for field in ("experiment", "condition"):
                    if not isinstance(identity.get(field), str) or not identity[field]:
                        raise PublishError(f"card identity.{field} must be a non-empty string")
                run = identity.get("run")
                if not isinstance(run, str) or not run:
                    raise PublishError("card identity.run must be a non-empty string")
                name = identity["experiment"]
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
                    raise PublishError("invalid experiment name")
                # A known run filtered out of this index is still present in the store.
                unseen.discard(run)
                if any(identity[field] not in store.filters[plural]
                       for plural, field in (("experiments", "experiment"), ("conditions", "condition"), ("runs", "run"))
                       if plural in store.filters):
                    continue
                if exclusion := exclusions.get(run):
                    print(f"EXCLUDE s3://{store.target.bucket}/{key}: {exclusion.reason}")
                    continue
                row = encode({"store": store.url, "card": card})
            except Exception as error:
                print(f"index: WARNING: skipping s3://{store.target.bucket}/{key}: {failure(error)}", file=sys.stderr)
                continue
            grouped.setdefault(name, []).append(row)
            count += 1
        for run in sorted(unseen):
            print(f"index: WARNING: excluded run {run} not found in "
                  f"s3://{store.target.bucket}/{store.target.prefix}", file=sys.stderr)
        print(f"STORE s3://{store.target.bucket}/{store.target.prefix} {count} runs ({cached} cached, {fetched} fetched)")
    return grouped


def read_catalog(directory: Path, names: list[str]) -> tuple[dict[str, Json], dict[str, Path]]:
    manifests: list[Json] = []
    hints: dict[str, Json] = {}
    shared: Json = None
    assets: dict[str, Path] = {}
    for name in sorted(names):
        path = directory / f"{name}.json"
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            print(f"index: WARNING: skipping missing manifest {path}", file=sys.stderr)
            continue
        manifest: Json = json.loads(raw)
        if not isinstance(manifest, dict):
            raise PublishError(f"{path}: manifest must be an object")
        schema = manifest.get("schema")
        schema_file = schema.get("path") if isinstance(schema, dict) else None
        if isinstance(schema, dict) and isinstance(schema_file, str):
            schema_path = Path(schema_file)
            hints[name] = {str(schema["version"]): json.loads(schema_path.read_bytes())}
            if shared is None:
                shared = json.loads((schema_path.parent / "shared-schema.json").read_bytes())
            del schema["path"]
        manifests.append(manifest)
        source = directory / "assets" / name
        if source.is_dir():
            assets[f"catalog/assets/{name}"] = source
    return {"v": 0, "manifests": manifests, "shared": shared if shared is not None else {}, "hints": hints}, assets


def build(stores: list[Store], directory: Path, catalog: Path, dry_run: bool, cache: Path | None = None) -> None:
    if not catalog.is_dir():
        raise PublishError(f"--catalog must be an existing directory: {catalog}")
    # Read every source before replacing any destination projection.
    grouped = collect(stores, cache, dry_run)
    manifests, assets = read_catalog(catalog, list(grouped))
    objects = {f"experiments/{name}/index.jsonl": b"".join(rows)
               for name, rows in sorted(grouped.items())}
    objects["catalog.json"] = encode(manifests)
    root = {"v": 0, "experiments": [{"name": name, "runs": len(rows)} for name, rows in sorted(grouped.items())],
            "runs": sum(len(rows) for rows in grouped.values()),
            "built_at": serialize_utc_datetime(datetime.now(timezone.utc))}
    objects["index.json"] = encode(root)  # advertise the rebuilt shards last
    if not dry_run:
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    for key, source in assets.items():
        # Path.walk follows the linkFarm and any nested asset-directory symlinks.
        for parent, dirs, files in source.walk(follow_symlinks=True):
            dirs.sort()
            for name in sorted(files):
                asset = parent / name
                path = directory / key / asset.relative_to(source)
                print(f"{'PLAN' if dry_run else 'WRITE'} {path} {asset.stat().st_size} bytes")
                if not dry_run:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(asset, path)
    for key, body in objects.items():
        path = directory / key
        print(f"{'PLAN' if dry_run else 'WRITE'} {path} {len(body)} bytes")
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def index_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="adb-runner index", description=__doc__)
    parser.add_argument("--stores", required=True, type=Path, metavar="FILE", help="YAML or JSON store list")
    parser.add_argument("--catalog", required=True, type=Path, metavar="DIR", help="manifest directory (<name>.json and assets/<name>/)")
    parser.add_argument("--to", required=True, metavar="DIR", help="index directory, deleted and rewritten in full")
    parser.add_argument("--dry-run", action="store_true", help="print filtered store counts, files and sizes; write nothing")
    parser.add_argument("--cache-dir", type=Path, metavar="DIR", help="card cache directory (default $XDG_CACHE_HOME/adb/index or ~/.cache/adb/index)")
    parser.add_argument("--no-cache", action="store_true", help="bypass reading and writing the card cache")
    args = parser.parse_args(argv)
    if "://" in args.to:
        parser.error("--to must be a local directory")
    try:
        cache = None if args.no_cache else resolve_cache_dir(args.cache_dir)
        build(read_stores(args.stores), Path(args.to), args.catalog, args.dry_run, cache)
        return 0
    except Exception as error:
        print(f"index: FAIL: {failure(error)}", file=sys.stderr)
        return 1
