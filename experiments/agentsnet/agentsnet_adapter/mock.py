"""The keyless mock responder for the authors' prompts (the smoke/CI path).

It plays a cooperative but unthinking agent against upstream's own prompts: it
reads its name and neighbours from the system prompt, sends one short message to
every neighbour each round in the JSON shape the prompt asks for, and answers
the final question with one of the valid options the prompt lists, chosen
deterministically from the run seed and its own name. It is a pure function of
the message list. It exercises upstream's loop, parsing and scoring; its replies
always parse, so re-prompts and unparseable answers are tested by wrapping it
(tests/test_cells.py). It says nothing about any real model.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from reps_experiment.llm import deterministic_pick

_OWN_NAME = re.compile(r"Your name is (\w+)\.")
_NEIGHBOURS = re.compile(r"immediate neighbors \(([^)]*)\)")
_ROUND_NEIGHBOURS = re.compile(r"Your neighbors are: (.*?)(?: These are the last|\s*$)", re.DOTALL)
_VALID = re.compile(r"valid options: (.*)$", re.DOTALL)


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def _names(csv: str) -> list[str]:
    return [name.strip() for name in csv.split(",") if name.strip()]


def mock_responder(seed: int) -> Callable[[list[dict[str, Any]]], str]:
    def respond(messages: list[dict[str, Any]]) -> str:
        system = "\n".join(_text(m) for m in messages if m.get("role") == "system")
        humans = [_text(m) for m in messages if m.get("role") == "user"]
        last = humans[-1] if humans else ""
        own = _OWN_NAME.search(system)
        own_name = own.group(1) if own else "agent"
        found = _NEIGHBOURS.search(system)
        neighbours = _names(found.group(1)) if found else []
        # the final question: "... Don't use any text for your final answer except one of these valid options: a, b"
        valid = _VALID.search(last)
        if valid:
            options = _names(valid.group(1))
            choice = options[deterministic_pick(seed, own_name, len(options))] if options else "None"
            return f"Thinking it through step by step: I weigh what my neighbours told me.\n\n### Final Answer ###\n{choice}"
        # a message round (or a re-prompt after an unparseable reply)
        in_round = _ROUND_NEIGHBOURS.search(last)
        if in_round:
            neighbours = _names(in_round.group(1)) or neighbours
        outgoing = {name: f"Hello {name}, this is {own_name}. My neighbours are {', '.join(neighbours)}." for name in neighbours}
        return "Step by step: I share who I am and who I can reach.\n\n```\n" + json.dumps(outgoing, indent=4) + "\n```"
    return respond
