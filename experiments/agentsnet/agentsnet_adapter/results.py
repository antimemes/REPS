"""The authors' per-run numbers and the per-round messages, read from their own
objects after the run.

Upstream keeps, per node, the full LangChain conversation (`chat_history`, the
`messages_to_dict` form) and the last parsed JSON object (`messages`); it
writes the former as `transcripts` in its results JSON. The per-round messages
are reconstructed by walking each transcript the way upstream's own
`chat_tool.py` reads it: every AI reply that follows a round prompt is parsed
with upstream's `parse_messages`, and a reply to a re-prompt replaces the one
that failed, so each round's object is the one upstream delivered.
"""

from __future__ import annotations

from typing import Callable, Optional

BOOTSTRAP = "What are the first messages you want to send"
ROUND = "These are the messages from your neighbors"
RETRY_MESSAGES = "Your messages could not be parsed"
FINAL = "Message passing has finished"
RETRY_ANSWER = "Your answer could not be parsed"


def round_messages(history: list[dict], parse: Callable[[str], Optional[dict]]) -> list[dict[str, str]]:
    """`history[i]` is a LangChain message dict ({"type": "human"|"ai"|"system",
    "data": {"content": ...}}). Returns one object per message round (index 0 =
    round 1), {} where upstream parsed nothing even after the re-prompt."""
    rounds: list[dict[str, str]] = []
    phase = None   # "messages" | "final" | None
    for entry in history:
        content = str(entry.get("data", {}).get("content", ""))
        if entry.get("type") == "human":
            if content.startswith(BOOTSTRAP) or content.startswith(ROUND):
                rounds.append({})
                phase = "messages"
            elif content.startswith(FINAL) or content.startswith(RETRY_ANSWER):
                phase = "final"
            elif content.startswith(RETRY_MESSAGES):
                phase = "messages"
            else:
                phase = None
        elif entry.get("type") == "ai" and phase == "messages" and rounds:
            parsed = parse(content)
            if parsed is not None:
                rounds[-1] = parsed
    return rounds


def summary(record: dict, model, rounds: int, *, crashed: bool = False) -> dict:
    """The results from upstream's record and task object. `score` is left out
    when upstream recorded null (an unsuccessful run). `crashed` marks upstream's
    empty-vertex-cover ZeroDivisionError, recorded with score 0."""
    out: dict = {
        "successful": bool(record["successful"]),
        "upstream_crashed": crashed,
        "rounds_run": rounds,
        "fallbacks": sum(model.num_fallbacks),
        "unparsed_messages": sum(model.num_failed_json_parsings_after_retry),
        "unparsed_answers": sum(model.num_failed_answer_parsings_after_retry),
    }
    if record.get("score") is not None:
        out["score"] = float(record["score"])
        out["solved"] = out["score"] == 1.0    # the paper's binary metric (Table 2)
    return out
