---
rfc: 2
title: Schema versioning
status: provisional
---

# RFC 0002: Schema versioning

## 0. Envelope

Every record carries:
- `v: NonNegativeInt` (record-writing version);
- `ts` (aware UTC capture datetime with six fractional digits and a trailing Z);
- `run: str` (unique execution identifier);
- `experiment: str` (schema family);
- `schema: NonNegativeInt` (a specific experiment's custom event union version);
- `seq: NonNegativeInt` (runner-assigned capture order); and
- `event` (the validated payload).

`(run, seq)` globally identifies a **record**.

## 1. Envelope and payload versions

`v` versions how records are written: the envelope, the meaning of common event types defined in `reps_events`, and how the repository's producers (e.g., `reps_experiment`) fill them.

`schema` identifies an experiment's custom events. The manifest declares `schema = { version, models }`, where `models` is a `module:attribute` pointer.

The manifest file's own `schema_version` is a third, independent version, 1 for ordered, named result declarations.

## 2. Compatibility and bumps

`v` and `schema` are bumped whenever old records would fail to validate under the models, or when deserializing old records into new models would lead to a misinterpretation.

A version bump is not required when:
- adding new union members; or
- adding new optional fields, so long as their default value corresponds to the behavior in deserialization of records prior to their introduction.

The `retries` fallback on `llm.call`, which reads `reps_experiment`'s legacy metadata markers, is a lossless parsing of old records into new models. It is grandfathered in as the `v: 0` reading; such migrations should be handled with a `v` version bump in the future.

Whenever possible, old records are losslessly migrated to the new format, in JSON, before validation.

## 3. Changelog

A changelog is kept for the history of `v` versions, and for each experiment's `schema` versions. Each version bump should add an entry in the corresponding changelog along with the same commit.

For `v`, the changelog is in Appendix A below, and the `v` constant in `reps_events` links here.

For `schema`, each experiment keeps its changelog in the README.md in its `experiments` subfolder, at `<repository-root>/experiments/<experiment-name>/README.md`.

Changelogs are prepend-only, sorted in descending order.

The changelog should follow the format:

```markdown
### N

**Change:** What a field or event now means, stated as the rule new readers apply.

**Reading records at N-1:** How a reader applies the old meaning to records of the previous version.
```

## Appendix A. `v` changelog

### 1

**Change:** `llm.call.error` now means that no model output was obtained. Before, it meant only that the client raised an exception, which conflated two different things: provider rejections that still carried the model's output, and genuine failures.

Azure's content filter exposed this. It blocks a response with HTTP 400 but puts the model's choice in the body under `finish_reason: content_filter`, whereas OpenAI reports the same block as a 200 with the same choice. The client recorded the Azure form as a failed call and the OpenAI form as output.

Precisely, following Inspect's definitions of the vendored fields: `llm.call.error` is set iff the model call failed and produced no usable output; `llm.call.call.error` is true iff the request failed with no response body captured; `llm.call.call.response` holds the response body whenever one was captured, whatever its HTTP status. A content filter is output with a `content_filter` stop, not a failure. Nothing is implied about `choices`: a failed call may carry a producer's placeholder choice.

The client builds `llm.call.output` from any body that contains `choices` through the same code path as a 200 response, including `llm.call.output.model`, `llm.call.output.usage` and each choice's `stop_reason`, so the Azure 400 and the OpenAI 200 produce identical records. An HTTP error without valid choices is recorded with its body and SDK error message and raised immediately, without entering the empty-response retry loop. Only a 2xx response with empty choices enters that loop; if every attempt is empty, the client records an empty-response error.

**Reading records at 0:** Two kinds of version 0 record disagree with this meaning.

- Failed calls whose body carried the model's output. `llm.call.call.response` was null and the body existed only inside `llm.call.error`, as the Python repr following `Error code: <status> - `. Parse the repr into `llm.call.call.response`. If it contains `choices`, build `llm.call.output` from it as the version 1 client would, set `llm.call.error` to null and `llm.call.call.error` to false, since a body was captured.
- Successful calls with no output. Azure grok-4.6 returned HTTP 200 with empty `choices` during two outages, and the version 0 client could neither retry nor flag them, so they have `llm.call.error` null and `llm.call.output.choices` empty. Such empty `choices` responses now have a retry mechanism, but for legacy records, set `llm.call.error` to `Empty choices for model '<llm.call.model>' (version 0 record, not retried)` and leave `llm.call.call.error` as written, since the response was captured.

When a legacy record has no `call`, migrate its `error` and `output` without inventing a request or adding `call`.

A version 0 error with the SDK prefix must migrate successfully: failure to parse its body as a Python literal and JSON-compatible data, or to convert its `choices` to output, is a migration error. Errors without the prefix remain unchanged.
