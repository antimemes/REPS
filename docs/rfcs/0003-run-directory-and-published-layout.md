---
rfc: 3
title: Run directory and published layout
status: draft
---

# RFC 0003: Run directory and published layout

## 0. Evidence versus convenience

The stream is evidence, frozen and addressed by `(run, seq)`. `run.json` is the runner's index card: a cache of stream facts plus lifecycle state and derived values. Everything on the card is a copy of a record or derivable from the stream, apart from the runner's operational live state; a test asserts the copies and derivations. The stream wins on disagreement. A fact that exists only on the card is not evidence, and nothing reads the card as an execution input.

## 1. Lifecycle records

`run.start` and `run.end` contain facts true at that moment, known by the runner and needed to interpret other records, never anything derived from other records. Start records identify the condition, inputs and launch provenance; end records give process state, duration and exit code. Results, token totals and activity counts belong in a projection of the stream, not in either lifecycle event.

## 2. The card

`identity` copies run, experiment and schema from the start envelope and condition from its payload; `inputs` copies params and the run seed; `lifecycle` holds runner-owned state, start/end capture timestamps and terminal process facts; `provenance` copies source, optional fetch_ref and tree_hash, and runtime; `definitions.results` copies the ordered result declarations; `derived` caches declared results (last value wins), model usage, counts, latest status and the last included event position. The runner replaces the card atomically at each live heartbeat and at run end; the file's modification time provides liveness without a stored heartbeat field. Consumers may use it for listings and summaries; its contents can always be checked against records. Batch facts belong only to the local jobs file, not the card or stream.

A condition is a grouping of run cards, not a stored object. Condition identity, experiment, source and params live on every card and `run.start`; readers group cards on `condition` and take shared fields from any member. These fields are identical by construction of the condition hash.

## 3. The run directory

A local run contains `run.json`, one `events.jsonl` stream file, and `workspace/`. The workspace holds local upstream working files and is never published. Evidence to retain from those files must first enter the stream through the producer's capture or ingestion boundary. There is one stream file per run, with sequence numbers identifying records independently of their byte positions.

## 4. Directory names

The final layout for local and published stores is `runs/<condition>-<experiment>/<run>/`, containing the card and stream (plus a local-only `workspace/`). There is no separate condition resource. The condition is the first 40 hexadecimal characters of SHA-256 over canonical JSON, as defined in [RFC 0004 §4](0004-identity-provenance-and-pooling.md#4-condition-as-pooling-key); the suffix is informational and readers construct it from recorded fields, never parse it. Run IDs use the readable UTC form `yyyymmddthhmmssz-<12 lowercase hex random>` from the launching machine's clock. The whole string is the ID, and its date is a launch-time label, not evidence.

## 5. Published layout

An experiment bucket contains only `runs/` under a caller-selected S3 prefix. Publication preserves the final `runs/<condition>-<experiment>/<run>/` structure. Conditions remain groupings of cards; no separate condition object is published.

```text
<prefix>/
  runs/
    <condition>-<experiment>/
      <run>/
        run.json
        events.jsonl.zst
```

Each `events.jsonl.zst` is one level-19 zstd frame, preserving the original JSONL bytes when decompressed, with `Content-Type: application/zstd`. `run.json` is the full local card, copied byte-for-byte with `Content-Type: application/json`. Workspaces are never published. Indexes are derived, live outside experiment buckets, and follow [§7](#7-indexes).

Publication requires a terminal state, a pinned clean `fetch_ref`, and a passing `verify` audit, including model identity checks. Failures are reported per run without aborting other publications. An upload is refused if either destination run key exists. The stream is uploaded first, then the card; partial uploads are never overwritten. Publication errors do not change run state or exit code.

Storage uses the caller's boto3 configuration: no endpoint, region or client configuration overrides, host presets, remote registry or target environment variable belong in ADB. Ambient AWS variables do not enter the experiment environment; explicitly supplied experiment credential sets are preserved.

## 6. Deferred

Chunked objects for live runs are deferred because record identity is independent of transport boundaries, so a later uploader can partition the same stream. Derived Parquet tables are deferred because they can be rebuilt from frozen records without changing producers. A fingerprint-keyed manifest registry is deferred because source fingerprints already identify the manifests to index; the registry adds a lookup location rather than changing saved records.

## 7. Indexes

Indexes are derived from complete card/stream pairs, rebuilt in full, never authoritative, and live outside experiment buckets. Original objects retain their bytes. A user-written YAML or JSON store list selects sources; ADB never infers public URLs from S3:

```yaml
v: 0
stores:
  - s3: s3://bucket/prefix
    profile: research
    url: https://data.example.org/prefix
    experiments:
      - govsim
    conditions:
      - condition-id
    runs:
      - run-id
      - excluded-run-id
    exclude:
      - runs:
          - excluded-run-id
        reason: Provider outage invalidated these results.
```

`s3` and optional `profile` select access; `url` is the public HTTPS base for the same prefix. Optional filters match exact recorded values and intersect; omission includes everything, an empty list nothing. An omitted profile uses boto3's normal resolution. Exclusions are site curation, recorded with reasons in the site's store list, and never touch bucket objects. The destination contains:

```text
index.json
experiments/<experiment>/index.jsonl
catalog.json
catalog/assets/<experiment>/
```

`index.json` has shape `{"v":0,"experiments":[{"name":"govsim","runs":1}],"runs":1,"built_at":"<UTC timestamp>"}`. Counts count rows; `built_at` uses the stream's six-fractional-digit UTC format. Each shard row has shape `{"store":"<public HTTPS base>","card":<run.json as a JSON value>}`. The index adds no fields to the card; its re-serialized value never substitutes for the original card bytes in raw display. Only indexed experiments contribute manifests, shared and versioned hints, and README assets. Missing manifests warn and leave run-only experiments visible. An experiment without indexed runs is absent.

The [publishing guide](../book/src/running/publishing.md) covers commands, deployment and browser behavior.
