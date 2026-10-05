---
rfc: 2
title: Schema versioning
status: provisional
---

# RFC 0002: Schema versioning

## 0. Envelope

Every record carries:
- `v: NonNegativeInt` (envelope shape and common events version);
- `ts` (aware UTC capture datetime with six fractional digits and a trailing Z);
- `run: str` (unique execution identifier);
- `experiment: str` (schema family);
- `schema: NonNegativeInt` (a specific experiment's custom event union version);
- `seq: NonNegativeInt` (runner-assigned capture order); and
- `event` (the validated payload).

`(run, seq)` globally identifies a **record**.

## 1. Envelope and payload versions

`v` versions the shared vocabulary: the envelope and the common event types defined in `adb_events`.

`schema` identifies an experiment's custom events. The manifest declares `schema = { version, models }`, where `models` is a `module:attribute` pointer.

The manifest file's own `schema_version` is a third, independent version, 1 for ordered, named result declarations.

## 2. Compatibility and bumps

`v` and `schema` are bumped whenever old records would fail to validate under the models, or when deserializing old records into new models would lead to a misinterpretation.

A version bump is not required when:
- adding new union members; or
- adding new optional fields, so long as their default value corresponds to the behavior in deserialization of records prior to their introduction.

The `retries` fallback on `llm.call`, which reads `adb_experiment`'s legacy metadata markers, is a lossless parsing of old records into new models. It is grandfathered in as the `v: 0` reading; such migrations should be handled with a `v` version bump in the future.

## 3. Changelog

A changelog is kept for the history of `v` versions, and for each experiment's `schema` versions. Each version bump should add an entry in the corresponding changelog along with the same commit.

For `v`, the changelog is in Appendix A below, and the `v` constant in `adb_events` links here.

For `schema`, each experiment keeps its changelog in the README.md in its `experiments` subfolder, at `<repository-root>/experiments/<experiment-name>/README.md`.

Changelogs are prepend-only, sorted in descending order.

The changelog should follow the format:

```markdown
### N

**Change:** What a field or event now means, stated as the rule new readers apply.

**Reading records at N-1:** How a reader applies the old meaning to records of the previous version.
```

## Appendix A. `v` changelog

None.
