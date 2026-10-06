"""The runner protocol: spawn the experiment, feed params, envelope + persist events.

runner → experiment: params JSON on stdin; REPS_RUN_ID/REPS_RUN_DIR/REPS_SEED env;
fresh workspace as cwd; a small env allowlist (never the full host environment).
experiment → runner: validated JSON events through REPS_EVENT_SOCKET; stdout and
stderr are always captured as text. The runner records typed envelopes and
acknowledges each socket submission after writing it to the event store.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TextIO
from urllib.parse import urlsplit

from . import credentials

from reps_events import (
    VOCABULARY_VERSION,
    Envelope,
    CapturedLine,
    Payload,
    Result,
    Log,
    RunStart,
    RunEnd,
    RunEnvironment,
)
from .schema import Manifest, Params
from reps_providers import PROVIDERS
from reps_events.event_socket import event_socket
from reps_events.version import VocabularyVersion
from .store import RunStore
from .card import CardProjection

# DOCKER_HOST: sandboxed experiments must find the machine's docker daemon (a
# per-user rootless socket on dev boxes — see `task docker:up`); like model
# endpoints, where the daemon lives is environment, never condition identity.
# Nothing else ambient — credentials/endpoints reach a run ONLY through the
# credential store (docs/book/src/running/secrets.md), so a stray key exported in the
# shell can neither leak into a run nor shadow the stored value.
ENV_ALLOWLIST = ["PATH", "HOME", "TERM", "TMPDIR", "DOCKER_HOST"]

# Refresh the index card from captured records while live, even during quiet periods.
HEARTBEAT_S = 10.0


def check_vocabulary_version(manifest: Manifest) -> VocabularyVersion:
    """Reject incompatible experiment/runner vocabularies before launch writes."""
    version = manifest.get("v", 0)
    if type(version) is not int or version != VOCABULARY_VERSION:
        raise ValueError(
            f"vocabulary version mismatch: runner v={VOCABULARY_VERSION}, manifest v={version}"
        )
    return VOCABULARY_VERSION


def validate_fetch_ref(ref: str | None) -> str | None:
    """Only launcher-supplied pinned references are publishable provenance."""
    if not ref:
        return None
    # Check URI authorities and scp-style references, without echoing credentials.
    if "@" in urlsplit(ref).netloc or re.match(r"[^/?#]*@", ref):
        raise ValueError("fetch_ref must not contain userinfo")
    # Old launchers used a dirty marker; it is not a fetchable revision.
    return None if ref.startswith("dirty:") else ref


def _store_path(path: str | None) -> str | None:
    return path if path and path.startswith("/nix/store/") else None


def _cpu_model() -> str:
    """Linux exposes the model in procfs; retain a platform description elsewhere."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            key, _, value = line.partition(":")
            if key.strip() in {"model name", "Hardware", "Processor"} and value.strip():
                return value.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _endpoint_origins(
    manifest: Manifest, params: Params, credential_env: dict[str, str],
) -> dict[str, str]:
    """Record configured endpoint origins, never URL paths or credentials."""
    endpoints: dict[str, str] = {}
    for name in sorted(credentials.sets_used(manifest, params)):
        provider = PROVIDERS.get(name)
        prefix = re.sub(r"[^A-Za-z0-9]+", "_", name).upper()
        variable = provider.base_url.name if provider else f"{prefix}_BASE_URL"
        url = credential_env.get(variable) or (
            provider.base_url.default
            if provider and credential_env.get(provider.api_key.name) else ""
        )
        if not url:
            continue
        try:
            parts = urlsplit(url)
            host = parts.hostname
            port = parts.port
        except ValueError:
            continue  # An invalid endpoint has no trustworthy origin to record.
        if parts.scheme not in ("http", "https") or not host:
            continue
        host = f"[{host}]" if ":" in host else host  # IPv6 URL authority.
        endpoints[name] = f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")
    return endpoints


def child_env(
    run_id: str, run_dir: str, seed: int, credential_env: dict[str, str] | None = None
) -> dict[str, str]:
    # constructed from scratch: system basics from the allowlist, then the stored
    # credentials/endpoints (credentials.py) — the store always wins over the
    # host — then REPS_* run vars. This is how a real model reaches its key without
    # the key ever appearing on the command line or being readable from the shell.
    env = {key: value for key, value in os.environ.items() if key in ENV_ALLOWLIST}
    env.update(credential_env or {})
    env.update(
        LANG="C.UTF-8",
        LC_ALL="C.UTF-8",
        REPS_RUN_ID=run_id,
        REPS_RUN_DIR=run_dir,
        REPS_SEED=str(seed),
    )
    return env


RunState = Literal["provisioning", "running", "completed", "failed", "interrupted"]


class RunResult:
    def __init__(
        self,
        run_id: str,
        state: RunState,
        duration_s: float,
    ):
        self.run_id = run_id
        self.state = state
        self.duration_s = duration_s


def execute_run(
    *,
    program: str,
    manifest: Manifest,
    params: Params,
    condition_id: str,
    source: str,
    fetch_ref: str | None = None,
    tree_hash: str | None = None,
    seed: int,
    store: RunStore,
    run_id: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    credential_env: dict[str, str] | None = None,
) -> RunResult:
    vocabulary_version = check_vocabulary_version(manifest)
    run_id = run_id or store.run_id
    if run_id != store.run_id:
        raise ValueError("run ID must match the store")
    fetch_ref = validate_fetch_ref(fetch_ref)
    # Resolve once before the launch snapshot; only host origins enter runtime.
    # The full URLs and keys are still passed unchanged to the child.
    if credential_env is None:
        credential_env = credentials.env_for_run(manifest, params)
    start = time.monotonic()
    seq = 0
    projection = CardProjection()
    seen_results: set[str] = set()
    result_definitions = deepcopy(manifest.get("results", []))
    declared_results = {result["name"] for result in result_definitions}
    events_q: queue.Queue[CapturedLine | Log | None] = queue.Queue()
    record_lock = threading.Lock()

    def write_card() -> None:
        store.write_run_json(projection.snapshot())

    def save_payload(payload: Payload) -> dict[str, Any]:
        """Write one envelope while record_lock is held."""
        nonlocal seq
        envelope = Envelope(
            v=vocabulary_version, ts=datetime.datetime.now(datetime.timezone.utc),
            run=run_id, seq=seq, event=payload,
            experiment=manifest["name"], schema=manifest.get("schema", {}).get("version", 0),
        )
        saved = envelope.model_dump(mode="json", by_alias=True, exclude_none=True)
        store.write_event(saved)
        projection.observe(saved)
        if isinstance(payload, RunStart):
            write_card()
        seq += 1
        return saved

    def record_payload(payload: Payload) -> None:
        # Socket handlers and stdio capture share one sequence and store writer.
        with record_lock:
            saved = save_payload(payload)
            warning = None
            if isinstance(payload, Result):
                if payload.name not in declared_results:
                    warning = f"Undeclared result {payload.name!r}; not declared in the manifest"
                else:
                    if payload.name in seen_results:
                        warning = f"Repeated result {payload.name!r}"
                    seen_results.add(payload.name)
            if on_event:
                on_event(saved)
            if warning is not None:
                saved_warning = save_payload(Log(level="warn", message=warning))
                if on_event:
                    on_event(saved_warning)

    runtime = RunEnvironment(
        platform=os.uname().sysname.lower() + "-" + os.uname().machine,
        cpu_model=_cpu_model(),
        cpu_count=os.process_cpu_count() or 1,
        experiment_bin=_store_path(program),
        runner_python_version=platform.python_version(),
        runner_bin=_store_path(os.environ.get("REPS_RUNNER_BIN")),
        endpoints=_endpoint_origins(manifest, params, credential_env),
    )
    record_payload(
        RunStart(
            result_definitions=[dict(declaration) for declaration in result_definitions],
            condition=condition_id,
            source=source,
            fetch_ref=fetch_ref,
            tree_hash=tree_hash,
            params=params,
            seed=seed,
            runtime=runtime,
        )
    )

    projection.card["lifecycle"]["state"] = "running"
    write_card()
    with event_socket(record_payload) as socket_path:
        proc = subprocess.Popen(
            [program],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=store.workspace,
            env={
                **child_env(run_id, str(store.dir), seed, credential_env),
                "REPS_EVENT_SOCKET": socket_path,
            },
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdin, stdout, stderr = proc.stdin, proc.stdout, proc.stderr
        if stdin is None or stdout is None or stderr is None:
            raise RuntimeError("child pipes missing")  # typeshed can't see Popen(PIPE)

        def read_output(stream: TextIO, kind: Literal["stdout", "stderr"]) -> None:
            try:
                for line in stream:
                    line = line.rstrip("\n")
                    if not line or (kind == "stdout" and not line.strip()):
                        continue
                    events_q.put(CapturedLine(type=kind, line=line))
            except Exception as exc:
                events_q.put(
                    Log(level="error", message=f"failed to capture {kind}: {exc}")
                )
            finally:
                events_q.put(None)

        threads = [
            threading.Thread(target=read_output, args=(stdout, "stdout"), daemon=True),
            threading.Thread(target=read_output, args=(stderr, "stderr"), daemon=True),
        ]
        for t in threads:
            t.start()

        try:
            stdin.write(json.dumps(params))
            stdin.close()
        except BrokenPipeError:
            pass

        interrupted = False
        capture_failed = False
        finished_readers = 0
        last_beat = time.monotonic()
        child_exited_at: float | None = None
        while finished_readers < 2 or proc.poll() is None:
            if time.monotonic() - last_beat >= HEARTBEAT_S:
                with record_lock:
                    write_card()
                last_beat = time.monotonic()
            if child_exited_at is None and proc.poll() is not None:
                child_exited_at = time.monotonic()
            try:
                item = events_q.get(timeout=0.5)
            except queue.Empty:
                # orphaned grandchildren can inherit our pipes and hold them open long
                # after the experiment exited — don't wait on them forever
                if (
                    child_exited_at is not None
                    and time.monotonic() - child_exited_at > 10
                ):
                    record_payload(
                        Log(
                            level="warn",
                            message="experiment exited but descendants still hold its "
                            "stdio pipes; closing the stream (orphans keep "
                            "running unsupervised)",
                        )
                    )
                    break
                continue
            except KeyboardInterrupt:
                interrupted = True
                proc.send_signal(signal.SIGTERM)
                continue
            if item is None:
                finished_readers += 1
                continue
            if isinstance(item, Log):
                capture_failed = True
            record_payload(item)

        returncode = proc.wait()
    duration = time.monotonic() - start
    state: RunState
    if interrupted or returncode < 0:
        state = "interrupted"
    elif returncode == 0 and not capture_failed:
        state = "completed"
    else:
        state = "failed"

    record_payload(
        RunEnd(
            state=state,
            duration_s=round(duration, 3),
            exit_code=returncode,
        )
    )
    with record_lock:
        write_card()
    store.close()
    return RunResult(run_id, state, duration)
