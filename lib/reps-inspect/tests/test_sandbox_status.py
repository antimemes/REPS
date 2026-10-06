"""sandbox_status: provisioning commands become status events, exec chatter doesn't."""

from __future__ import annotations

import json
import logging

from reps_inspect.sandbox_status import docker_verb, sandbox_provisioning_status

COMPOSE = (
    "docker compose --ansi never --progress quiet "
    "-f /tmp/compose.yaml -p inspect-abc123"
)


def test_docker_verb_provisioning():
    assert docker_verb(f"{COMPOSE} build") == "docker compose build"
    assert (
        docker_verb(f"{COMPOSE} pull --ignore-buildable --policy missing " "default")
        == "docker compose pull"
    )
    # internal images build with plain docker
    assert docker_verb("docker build -t img /ctx") == "docker build"
    assert docker_verb("docker pull aisiuk/inspect-tool-support") == "docker pull"


def test_docker_verb_excludes_noise():
    # per-tool-call and per-sample lifecycle commands stay out of the stream
    assert docker_verb(f"{COMPOSE} exec -T default bash -c ls") is None
    assert docker_verb(f"{COMPOSE} up --detach --wait") is None
    assert docker_verb(f"{COMPOSE} down --volumes") is None
    assert docker_verb("docker image inspect img") is None
    assert docker_verb("python -m pytest") is None
    assert docker_verb("") is None


def _trace_record(detail: str, event: str, **extra) -> logging.LogRecord:
    """A record shaped like inspect's trace_action output: message text plus
    structured attrs (the handler reads only the attrs)."""
    record = logging.LogRecord(
        "inspect_ai.util._subprocess",
        5,
        __file__,
        0,
        f"Subprocess: {detail} ({event})",
        None,
        None,
    )
    record.action = "Subprocess"
    record.detail = detail
    record.event = event
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _emitted(records, event_capture) -> list[dict]:
    with sandbox_provisioning_status():
        logger = logging.getLogger("inspect_ai.util._subprocess")
        for r in records:
            logger.handle(r)
    return event_capture.read()


def test_handler_emits_enter_and_exit(event_capture):
    events = _emitted(
        [
            _trace_record(f"{COMPOSE} pull default", "enter"),
            _trace_record(f"{COMPOSE} pull default", "exit", duration=312.04),
            _trace_record(f"{COMPOSE} exec -T default ls", "enter"),  # ignored
        ],
        event_capture,
    )
    assert [e["type"] for e in events] == ["status", "status"]
    assert events[0]["detail"] == "sandbox: docker compose pull running…"
    assert events[1]["detail"] == "sandbox: docker compose pull done (312.0s)"


def test_handler_reports_failure_outcomes(event_capture):
    events = _emitted(
        [
            _trace_record(f"{COMPOSE} build", "timeout", duration=600.0),
        ],
        event_capture,
    )
    assert events == [
        {"type": "status", "detail": "sandbox: docker compose build timeout (600.0s)"}
    ]


def test_handler_detaches(event_capture):
    _emitted([], event_capture)  # attach/detach cycle
    logger = logging.getLogger("inspect_ai")
    assert not any(type(h).__name__ == "_ProvisioningHandler" for h in logger.handlers)
