# Experiment process protocol

This page describes the underlying experiment program passed as `program` to
`adb.mkExperiment`. It receives one parameter object and emits event payloads.
To start a run, use the generated named experiment app;
[launch commands](../running/experiments.md#how-do-i-use-the-command-line) describe
the user-facing entry points. The program itself does not launch `adb-runner`. The runner supplies execution IDs, stores records and determines the process outcome.

## What does the runner provide?

The runner invokes the executable without additional arguments, sets its working directory to the run's fresh `workspace/`, writes the realized parameters as JSON on stdin, then closes stdin.

| Environment variable | Meaning |
| --- | --- |
| `ADB_RUN_ID` | This run's whole execution ID (UTC launch label plus random suffix). |
| `ADB_RUN_DIR` | Run directory containing metadata, the event stream and `workspace/`. |
| `ADB_SEED` | Derived run seed as a decimal string. |
| `ADB_EVENT_SOCKET` | Per-run Unix stream socket for structured events. |

The child environment starts with only the host values of `PATH`, `HOME`, `TERM`, `TMPDIR` and `DOCKER_HOST`. The runner adds values from selected credential profiles, pins `LANG` and `LC_ALL` to `C.UTF-8`, then adds its run variables. Arbitrary exported keys and endpoints are not inherited.
The experiment records its own interpreter, libc and effective locale in
[`producer.python`](events.md#producer-toolchain), separately from the runner's runtime snapshot.

A fresh directory and restricted environment are not an operating-system sandbox. The program runs as the launching user and can access resources available to that user. Some experiments use their own Docker-backed tasks; their daemon and service requirements must be met separately.

## What should the program emit?

Use the language-neutral `adb-emit` CLI, or Python's `adb_events.emit(model)`,
to record structured events. The helpers handle the transport; experiment
authors should not implement socket clients. For example, an experiment
can invoke:

```sh
adb-emit result --name count --value 3
```

Both helpers validate the payload and wait for the runner to acknowledge it.
The runner independently validates against `ProducerPayload`, adds the
[transport envelope](events.md#transport), and records the event. Unknown types,
missing required fields, undeclared fields, malformed payloads, and producer
attempts to emit runner lifecycle events are rejected with an error response.
They never become invalid records in the JSONL stream.

All nonempty stdout/stderr lines from the experiment program become `stdout`/`stderr` events with a `line`
field, including lines that happen to contain JSON objects. Whitespace-only
stdout lines are ignored. Ordinary prints cannot submit structured events.
These are UTF-8 text channels: malformed bytes are replaced with `�` so capture
continues. Store binary output in artifact files. An unexpected capture failure
is recorded as an error and makes the run fail.

## How does the run finish?

The runner drains captured output and waits for the process. Exit code zero produces `completed`; a nonzero code produces `failed`; a signal exit or handled interrupt produces `interrupted`. It then emits `run.end` and writes final process metadata. Readers derive results and usage from the event stream.

The state reports the process outcome. An adapter that catches errors and returns zero should emit metrics, logs or instance errors that make the research outcome clear. A hard runner crash may leave an active state and an incomplete stream.

## How can Python experiments emit validated events?

Construct models from `adb-events` and pass them to `emit`:

```python
from adb_events import CustomEvent, Result, emit

emit(CustomEvent(kind="govsim.record", data={
    "action": "utterance", "agent_id": "agent-1", "utterance": "I choose option A.",
}))
emit(Result(name="choices", value=1))
```

Models validate at construction: wrong types and unknown fields raise a Pydantic
`ValidationError`. Use the declared `metadata`, `meta`, `data`, `params`, and raw `call`
objects for open JSON metadata. They preserve their contents, including nulls.
`emit` serializes once, validates that JSON with strict mode, and writes the
exact same JSON. Validation uses the public `ProducerPayload` union and can reject the event but cannot rewrite its payload. Absent optional model fields are omitted; explicit nulls in open dictionaries are retained. Public producer classes and typed CustomEvent subclasses are accepted; other subclasses and runner lifecycle models are rejected.

Model calls expose their nested models directly:

```python
from adb_events import (
    ChatMessageUser, ChatMessageAssistant, ChatCompletionChoice,
    LLMCall, ModelCall, ModelOutput, emit,
)

emit(LLMCall(
    agent="agent-1", model="provider/alias",
    input=[ChatMessageUser(content="Hello")],
    output=ModelOutput(
        model="provider-returned-model", completion="Hi",
        choices=[ChatCompletionChoice(
            message=ChatMessageAssistant(content="Hi"), stop_reason="stop",
        )],
    ),
    call=ModelCall(request={"model": "model-sent-to-sdk", "temperature": 0.2, "max_tokens": 128},
                   response={"system_fingerprint": "fp_123"}),
))
```

The chat and output data models are vendored from Inspect AI 0.3.263 in
`adb_events.inspect_chat`; analysis needs only Pydantic. `input` contains typed
messages and `output` retains every choice, usage, and output metadata. Raw SDK
request/response evidence lives in `call`. Read generation settings from
`call.request`; no separate projection is stored. The direct OpenAI client
records effective SDK settings after adapter overrides. The Inspect adapter
retains `GenerateConfig` only in its raw `inspect.event` record.

For data that does not fit a standard model, use the public custom container:

```python
from adb_events import CustomEvent, emit

emit(CustomEvent(kind="my-experiment.resource", data={"remaining": 42}))
```

Experiments can subclass `CustomEvent[DataModel]` with a literal `kind` and
strict Pydantic data. Their Payload union replaces the untyped custom arm with
these kinds. Generic custom data still accepts JSON objects, arrays and nulls.
Use prefixed custom kinds for per-item outcomes and attributed actions, and
`Log` for diagnostics. The shared [emission test fixture](../authoring/experiments.md#how-do-i-test-python-emission-without-launching-a-full-run)
works for either form.

`adb_experiment.scaffold.deposit_artifact` writes a text artifact and emits its
pointer using the required experiment-specific `kind` argument. Its `experiment_main` helper reads and validates parameters, reports
validation or run-function exceptions to stderr, and returns `1` on those
errors. A run-function exception also emits any supplied fallback metrics.
Successful execution returns `0`.

For other languages, invoke `adb-emit`, installed by `adb-events` and also included in the runner's environment. It handles validation and delivery for you. The [event reference](events.md#emission-tools) describes that CLI and its schema output.


## Reading runs in Python

`Payload` is the complete discriminated union, including runner lifecycle
events. `ProducerPayload` is the subset experiments can emit. `Envelope`
contains the transport fields and an `event: Payload`.

The runner's internal `record_payload` function constructs the same typed
`Envelope` that readers use. Socket submissions must validate before reaching it.

```python
from adb_events import CustomEvent, LLMCall, read_events

for record in read_events("/path/to/run"):
    event = record.event
    if isinstance(event, LLMCall):
        print(record.seq, event.model, event.output.completion)
    elif isinstance(event, CustomEvent):
        print(event.kind, event.data)
```

`read_events` accepts a run directory or its `events.jsonl` file. The default shared reader needs no Inspect dependency or experiment imports.
For typed GovSim data, pass `payload=govsim_adapter.models.Payload`; a
Pydantic TypeAdapter is also accepted.
For static type inference, construct `TypeAdapter[GovsimPayload](GovsimPayload)`
and pass that adapter; bare runtime union aliases validate identically.
Invalid records raise `EventReadError` with the file and line number, including
unknown types and truncated JSON. Valid partial runs can be read without a
`run.end` record. Files are never modified.

For a standalone payload JSON string, use `parse_event(json_text)`.
`EVENT_ADAPTER` exposes Pydantic's `TypeAdapter[Payload]` for bulk tooling;
`Envelope.model_validate_json(line, strict=True)` decodes a single saved record.
`adb-emit schema --union` exports the payload union's JSON Schema;
`adb-emit schema --envelope` exports the complete record schema.

## Wire protocol reference for emitter maintainers

Experiment authors should use `adb-emit` or `adb_events.emit()`. This section
documents the implementation boundary for maintaining those helpers and the
runner; it is not an additional experiment integration step.

The runner creates a filesystem Unix stream socket in a private, short temporary
directory before launching the experiment. This works on Linux and macOS.
For each event, connect to `ADB_EVENT_SOCKET`, send one UTF-8 JSON object followed
by a newline, and read one newline-terminated JSON response. Each connection
carries exactly one event; concurrent emitters use separate connections.

The response is `{"ok":true}` after the event store has written and flushed the
record, or `{"error":"..."}` on validation or recording failure. Acknowledgement
does not promise an `fsync` to physical storage. Events are limited to 64 MiB,
including the trailing newline. Socket operations time out after 30 seconds.
The runner closes the listener and finishes active handlers before recording
`run.end`, then removes the socket's temporary directory.

Python emission raises `EventTransportError` on missing configuration, failed
delivery, or rejection. `adb-emit` reports the error on stderr and exits with code
2; it writes nothing to stdout on successful emission. There is no automatic
retry: a lost acknowledgement can leave receipt uncertain, and retrying could
duplicate an event. The generated experiment launcher supplies the execution
context; use the named experiment app to start a run.
Schema commands work without a running experiment.
