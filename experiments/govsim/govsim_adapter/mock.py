"""A scenario-aware mock responder for GovSim's prompt chains.

Each GovSim parse rejects ChatClient's default mock lines, which makes each
gen/find/select fall back to ``ModelWandbWrapper``'s default values (harvest 0,
limit -1) — a degenerate all-defaults run. This responder keys on the format
markers GovSim's prompt functions put in the chat (markers are the same
across the fishing/sheep/pollution scenarios) and answers in the same shapes
pathfinder's regex parsing consumes, thus the mock run exercises the real
parse/aggregate paths.

The policy is intentionally boring: each persona harvests 5 units/month.
5 agents x 5 units from a pool of 100 does not exceed supply, thus the resource
allocation RNG does not fire and the run is
deterministic in practice: 12 rounds, no collapse, equal harvests.

Pure function of (messages) — determinism rides on the prompts alone.
"""

from __future__ import annotations

HARVEST_ANSWER = (
    "Considering the current state of the shared resource and the agreement to "
    "keep individual takes modest, a moderate amount is the wise choice this "
    "month. Answer: 5."
)

UTTERANCE = (
    "Response: I suggest we each keep our harvest modest this month so the "
    "resource can regenerate for all of us.\n"
    "Conversation conclusion by me: yes"
)

SUMMARY = (
    "The group agreed that everyone keeps their individual harvest modest so "
    "the shared resource can regenerate."
)

INSIGHT = (
    "I should keep my harvest conservative so the shared resource can recover "
    "(because of 1)."
)

TAKEAWAY = "I will remember that the group agreed to keep harvests modest."

IMPORTANCE = "5"

FALLBACK = "Answer: 5. Conversation conclusion by me: yes."


def govsim_mock_responder(messages: list[dict]) -> str:
    """messages is the pathfinder chat: the last entry can be a partially-filled
    assistant message (the continuation shape); the cue markers live in the last
    user message."""
    text = "\n".join(
        str(m.get("content", ""))
        for m in messages[-2:]  # last user message + an assistant partial
    )

    # group-chat utterance (converse_prompts: Output format .. Conversation
    # conclusion by me: [yes/no])
    if "Conversation conclusion by me" in text:
        return UTTERANCE
    # harvest quantity (act_prompts) and the framework's limit extraction
    # (reflect_prompts) — the two end with the Answer: instruction
    if 'Put the final answer after "Answer:"' in text:
        return HARVEST_ANSWER
    # memory importance ratings (store_prompts, select from 1..10)
    if "Rate the significance" in text:
        return IMPORTANCE
    # framework conversation summary (converse_prompts)
    if "Summarize the conversation above in one sentence" in text:
        return SUMMARY
    # reflection insights (reflect_prompts, numbered-evidence parsing)
    if "What high-level insights" in text:
        return INSIGHT
    # planning thought / memorize-from-conversation (reflect_prompts, gen to ".")
    if "you need to remember for your planning" in text:
        return TAKEAWAY
    if "you might have found interesting" in text:
        return TAKEAWAY
    return FALLBACK
