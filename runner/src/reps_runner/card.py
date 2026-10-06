"""Rebuildable run index card, derived only from the stream, never render hints.

Never read this cache to execute an experiment.
"""

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from reps_events import Envelope


class CardProjection:
    """Fold only successfully written stream records; snapshots share no mutable data."""

    def __init__(self):
        self.card: dict[str, Any] = {}
        self.declared: set[str] = set()
        self.derived: dict[str, Any] = {
            "results": {}, "served_models": [], "usage": {"input_tokens": 0, "output_tokens": 0},
            "counts": {"llm_calls": 0, "failed_calls": 0,
                       "by_kind": {}, "llm_calls_by_agent": {}},
        }

    def observe(self, record: dict[str, Any]) -> None:
        event = record["event"]
        tag = event["type"]
        if tag == "run.start":
            self.card = {
                "identity": {"run": record["run"], "experiment": record["experiment"],
                             "schema": record["schema"], "condition": event["condition"]},
                "inputs": {key: deepcopy(event[key]) for key in ("params", "seed")},
                "provenance": {key: deepcopy(event[key]) for key in
                               ("source", "fetch_ref", "tree_hash", "runtime") if key in event},
                "definitions": {"results": deepcopy(event["result_definitions"])},
                "lifecycle": {"state": "provisioning", "started_at": record["ts"]},
            }
            self.declared = {item["name"] for item in event["result_definitions"]}
        elif tag == "run.end":
            self.card["lifecycle"].update(
                {key: event[key] for key in ("state", "duration_s", "exit_code")},
                finished_at=record["ts"],
            )
        counts = self.derived["counts"]
        kind = event.get("kind", tag) if tag == "custom" else tag
        counts["by_kind"][kind] = counts["by_kind"].get(kind, 0) + 1
        if tag == "result":
            if event["name"] in self.declared:
                self.derived["results"][event["name"]] = event["value"]
        elif tag == "llm.call":
            served = event.get("output", {}).get("model")
            if served and served not in self.derived["served_models"]:
                self.derived["served_models"].append(served)
                self.derived["served_models"].sort()
            counts["llm_calls"] += 1
            counts["failed_calls"] += int(event.get("error") is not None)
            if isinstance(agent := event.get("agent"), str):
                counts["llm_calls_by_agent"][agent] = counts["llm_calls_by_agent"].get(agent, 0) + 1
            usage = event.get("output", {}).get("usage", {})
            # Inspect's input count excludes cache reads and writes.
            self.derived["usage"]["input_tokens"] += sum(usage.get(key, 0) for key in
                ("input_tokens", "input_tokens_cache_read", "input_tokens_cache_write"))
            self.derived["usage"]["output_tokens"] += usage.get("output_tokens", 0)
        elif tag == "status":
            self.derived["last_status"] = event["detail"]
        self.derived.update(last_seq=record["seq"], last_event_at=record["ts"])

    def snapshot(self) -> dict[str, Any]:
        return deepcopy({**self.card, "derived": self.derived})


def derive_card(records: Iterable[Envelope[Any]]) -> dict[str, Any]:
    """Rebuild from read_events; live state and the file's heartbeat mtime are runner-owned."""
    projection = CardProjection()
    for record in records:
        projection.observe(record.model_dump(mode="json", exclude_none=True))
    return projection.snapshot()
