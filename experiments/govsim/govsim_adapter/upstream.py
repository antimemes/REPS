"""Ingest final upstream JSON files without projecting or coercing native rows.

``persona_*/embeddings.json`` is omitted: it is recomputable from node
descriptions and the embedder named in ``govsim.config``. Each file's marker
hashes the original bytes, not a JSON reserialization. A malformed file has zero
ingested records and retains its text in
``govsim.unparsed_log``; other files are still captured before the error is raised.
"""

import hashlib
import json
from pathlib import Path

from reps_events import emit

from .models import (
    ACTION_MODELS, GovsimMemory, GovsimRecord, GovsimUnparsedLog, GovsimUpstreamLog,
    MemoryData, RecordData, UnparsedLogData, UpstreamLogData,
)


def _ingest_file(path: Path, storage: Path, persona: str | None = None) -> list[dict]:
    source = path.relative_to(storage).as_posix()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        # There are no bytes to hash when a checkpoint is missing or unreadable.
        emit(GovsimUnparsedLog(data=UnparsedLogData(
            text="", error=f"Cannot read {source}: {exc.strerror}",
        )))
        raise
    marker = UpstreamLogData(source=source, bytes=len(raw),
                             sha256=hashlib.sha256(raw).hexdigest(), records=0)
    try:
        rows = json.loads(raw.decode("utf-8"))
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"GovSim {source} must be a JSON array of records")
        # Validate the whole file before emitting its marker or any of its rows.
        records = [RecordData.model_validate(row) for row in rows]
    except ValueError as exc:
        emit(GovsimUpstreamLog(data=marker))
        emit(GovsimUnparsedLog(data=UnparsedLogData(
            text=raw.decode("utf-8", errors="replace"), error=f"Cannot parse {source}: {exc}",
        )))
        raise
    marker.records = len(records)
    emit(GovsimUpstreamLog(data=marker))
    for record in records:
        if persona is None:
            action = record.root.get("action")
            model = ACTION_MODELS.get(action, GovsimRecord) if isinstance(action, str) else GovsimRecord
            emit(model(data=record))
        else:
            emit(GovsimMemory(data=MemoryData(persona=persona, node=record)))
    return rows


def ingest_storage(storage: Path) -> list[dict] | None:
    """Capture the environment log then every persona's nodes, in file order.

    The returned environment rows feed result calculations. Report a missing log
    or missing nodes in an existing persona directory, then keep capturing other
    checkpoints before raising. No embeddings are read.
    """
    log = storage / "log_env.json"
    files = [log] + [directory / "nodes.json" for directory in sorted(storage.glob("persona_*"))
                    if directory.is_dir()]
    rows = None
    failure = None
    for path in files:
        try:
            ingested = _ingest_file(path, storage, None if path == log else path.parent.name)
            if path == log:
                rows = ingested
        except (ValueError, OSError) as exc:
            # A broken checkpoint must not hide other personas' completed files.
            if failure is None:
                failure = exc
    if failure is not None:
        raise failure
    return rows
