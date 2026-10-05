---
rfc: 1
title: Event basics and model API instrumentation
status: draft
---

# RFC 0001: Event basics and model API instrumentation

ADB records strict Pydantic payloads in a runner-owned JSONL stream. Experiments define their own typed custom observations alongside a small shared technical vocabulary. Analysis requires Pydantic, with no Inspect or OpenTelemetry runtime.

## 0. Shared v0 vocabulary

The shared wire tags are exactly these ten:

| Tag | Meaning |
| --- | --- |
| `run.start` | The runner begins launching an execution and snapshots provenance and inputs. |
| `run.end` | Terminal process state, duration and exit code. |
| `llm.call` | One observed model API operation, its effective request and available outcome. |
| `custom` | An experiment-defined observation discriminated by its namespaced `kind`. |
| `result` | One scalar result declared by the experiment's manifest. |
| `status` | the producer's current human-readable progress phrase; latest wins; structured progress belongs in custom events. |
| `log` | A deliberate diagnostic with `debug`, `info`, `warn` or `error` severity. |
| `stdout` | A captured stdout line, including text that happens to be JSON. |
| `stderr` | A captured stderr line; stderr alone does not establish failure. |
| `producer.python` | The producer's own toolchain, once, first: implementation, version, executable, platform, libc, locale, hash seed, flags. Per-language tags `producer.<language>` are owned by that language's events library. |

Only the runner emits lifecycle records. Producers submit validated payloads through `adb_events.emit` or `adb-emit`. A valid partial transcript may lack `run.end`; absence alone establishes neither success nor continued execution. The [events reference](../book/src/reference/events.md) specifies the fields.

`condition` is the single deliberate derived value in the stream, a hash of experiment, source and params on the same record, kept because it is the identity readers use to locate and pool runs; it is not precedent for other derived values.

## 1. Model calls

`LLMCall` carries `model`, `input`, `tools`, `tool_choice`, `output`, `call`, `error`, `retries`, `completed`, `working_time`, `metadata`, and ADB's `agent` attribution. Chat, content, citation, tool, output and usage models come from Inspect AI 0.3.263, vendored in [inspect_chat.py](../../lib/adb-events/adb_events/inspect_chat.py).

**Boundary rule.** `llm.call` holds what crossed the model API, plus ADB timing and agent attribution. Harness-only facts are recorded raw by that harness's adapter. Fields earn a place as normalized projections (`tools`, `tool_choice`) or facts not derivable from raw payloads (`agent`). Read generation settings from `call.request`. Messages and calls are expanded.

`agent` is the backend's supplied attribution; Inspect uses the model role when set, otherwise caller attribution, never per-sample scope. Each Inspect event is recorded raw, immediately followed by `llm.call` for model events, including cached events. `ToolCall.parse_error` preserves parsing evidence; `ToolCallError.type` accepts API or harness error types.

**Metadata rule.** Notes use producer-prefixed keys. Shared readers depend on no key; a key wanted by two producers becomes a field. The viewer does not read metadata.

Capture preserves all available choices, tools, multimodal content, detailed usage and provider metadata. `call.request` and `call.response` are serialized SDK objects, not HTTP bytes. Tool requests do not establish execution. `input_tokens` excludes cached tokens; add cache read/write counts for total input usage. Missing usage is unknown.

Failed requests retain available input and error. The optional non-negative integer `retries` field records observed HTTP 429/5xx responses, including the final rejection on exhaustion. Zero means none; it defaults to `None` and is omitted from JSONL when unknown. Connection failures and timeouts are not counted. This promotes the former `adb_experiment.retries` metadata note to a typed field. The Python `retries` property prefers that field and falls back to the historical metadata note when absent or null, then to zero if the historical `adb_experiment.backend` key is present, without rewriting the stored record. Internal SDK attempts become separate calls only if observed individually. Completion-time capture cannot preserve an operation whose process dies awaiting a response. The first well-formed `<think>…</think>` block in assistant text is reasoning (Inspect's rule), provider `reasoning_content` is reasoning, and everything else is text, untrimmed. GovSim trims at its Pathfinder backend for parity with local generation and anchored parsing. `call.response` retains the original SDK response, including its unmodified content strings.

## 2. Results

`Result` has `type="result"`, `name: str`, and `value: Scalar`: a finite number, string or boolean. Its name refers to a manifest result declaration, which owns the description and units. Series positions belong in custom observations, such as `govsim.state.resource`, rather than in results.

The runner retains every result event and validates names against the manifest. An undeclared name or repeated declared name produces a warning log. The runner derives the summary from the last result event per declared name, excluding undeclared names and leaving missing results absent. Consumers read results, call counts and usage totals from `run.json`'s `derived` section, maintained by the runner. `run.end` contains only process facts: `state`, `duration_s`, and `exit_code`; it carries no derived summary or usage totals.

## 3. Stream and experiment schemas

The envelope, timestamps, compatibility and frozen-module rules are specified in [RFC 0002](0002-schema-versioning.md); identity is specified in [RFC 0004](0004-identity-provenance-and-pooling.md). Optional model values are omitted with `exclude_none=True`; explicit nulls in open JSON objects retain their meaning. Emission validates the exact serialized payload it writes.

`CustomEvent` has `type="custom"`, `kind`, and JSON object `data`. Experiments specialize it with strict data models and literal kinds, exporting a full `Payload` union discriminated first on `type`, then on custom `kind`. [GovSim schema 0](../../experiments/govsim/govsim_adapter/models.py) is the first such union. `read_events(run_dir, payload=Payload)` accepts that type or its Pydantic `TypeAdapter`; the default shared union reads custom data generically. Invalid or truncated records raise an error identifying the file and line.

## 4. Deferrals

A tag is shared when the runner or verifier acts on it; promoting a custom kind is a read-time projection declared in adb-events, never a rewrite; cross-experiment views over raw kinds are projections, not tags.

Deferred names are `instance`, `artifact`, `agent.event`, `tool.call`, `span.begin`, `span.end`, `limit`, and raw harness records. Scientific concepts, including experiment-specific agent actions, remain typed custom events. Deferred concepts do not add tags to this v0 vocabulary.
