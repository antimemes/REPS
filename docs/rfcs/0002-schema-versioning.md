---
rfc: 2
title: Schema versioning
status: draft
---

# RFC 0002: Schema versioning

## 0. Envelope

Every record carries:
- `v: int` (envelope shape, 0);
- `ts` (aware UTC capture datetime);
- `run: str` (execution identifier);
- `experiment: str` (payload schema family);
- `schema: NonNegativeInt` (that experiment's payload union version);
- `seq: NonNegativeInt` (runner-assigned capture order); and
- `event` (the validated payload).

`(run, seq)` identifies a record; sequence alone does not prove causal order across processes. `ts` always has six fractional digits and a trailing Z; naive input is rejected. `LLMCall.completed` uses the same UTC type and serializer. Python spells the schema attribute `schema_`; JSON uses `schema`.

## 1. Envelope and payload versions

`v` versions only the envelope shape. An experiment's integer `schema` identifies its full payload union, including shared vocabulary and its custom events. The manifest declares `schema = { version, models }`, where `models` is a `module:attribute` pointer; GovSim uses version 0 and `govsim_adapter.models:Payload`. The runner stamps experiment and version on every record. The manifest file's own `schema_version` is a third, independent version, 1 for ordered, named result declarations. Readers select the appropriate union without rewriting saved records.

## 2. Compatibility and bumps

A schema version bumps only when records already written would fail to deserialize under the new module. Adding optional fields, union members, or literal values never bumps the version. Additive changes go in the current schema module; readers use its latest compatible library. The same compatibility rule applies to `v` for envelope changes. Incompatible changes require a new schema module and an experiment schema-version bump, retaining the old module for old records. Adding a raw harness record event alone therefore neither creates GovSim schema 1 nor changes existing GovSim run bytes.

## 3. Frozen-module guard

Frozen schema modules are never edited. The guard in [test_frozen.py](../../lib/adb-events/tests/test_frozen.py) compares SHA256 hashes of `inspect_chat.py` and `models/llm.py` with [frozen.json](../../lib/adb-events/tests/frozen.json), including comments and provenance. Do not refresh those hashes to admit edits to published snapshots. The current schema module is the extensible entry point: compatible declarations and union additions there compose around unchanged frozen models. This permits additive evolution without changing snapshots or bumping the schema version.
