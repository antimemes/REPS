"""Concordia's run record -> REPS events. A pure function of plain data (the roster, the
premise, and one (step, actor, action) tuple per turn), so it needs no Concordia import
and its test can feed hand-built inputs.

Opening narration, agent actions, and observations use experiment-specific custom
events. Their meanings follow Concordia's recorded data.
"""

from __future__ import annotations

from reps_events import CustomEvent, Result, emit

# The narrator's display name — sits next to roster names ("Alice", "Bob") in the
# transcript, so it's spelled like one. main.py names the Concordia game-master
# entity with the same string, so every GM-attributed event carries one name.
GM_NAME = "Game Master"


def emit_provenance(*, concordia_version: str, model: str,
                    agents: int, python_version: str) -> None:
    """Record Concordia/Python versions, requested model, and agent count.

    The runner separately records the scenario parameters.
    """
    emit(
        CustomEvent(
            kind="concordia.provenance",
            data={
                "agent": "concordia",
                "concordia": concordia_version,
                "model": model,
                "agents": agents,
                "python": python_version,
            },
        )
    )


def _strip_leading_name(actor: str, action: str) -> str:
    """Clean an action into a spoken line: drop the actor-name prefix Concordia adds
    ("Alice: hello" / "Alice hello" — `from` already carries the actor), the "-- " speech
    marker, and matched wrapping quotes. Returns empty if nothing meaningful remains (the
    caller skips empty turns)."""
    text = action.strip()
    if text.startswith(actor):
        text = text[len(actor):].lstrip(":").strip()
    text = text.lstrip("-–—").strip()
    if len(text) >= 2 and text[0] in "\"“'" and text[-1] in "\"”'":
        text = text[1:-1].strip()
    return text


def emit_scene(premise: str) -> int:
    """Emit the opening premise as a concordia.scene event. Returns 1, or 0 if the
    premise is empty."""
    if not premise:
        return 0
    emit(
        CustomEvent(kind="concordia.scene", data={"narrator": GM_NAME, "text": premise})
    )
    return 1


def emit_turn(step: int, actor: str, action: str, roster: set[str]) -> int:
    """Retain one resolved agent action as a custom event, live. Skips
    setup/skip phases (a non-roster actor) and empty actions. Returns 1 if emitted, else 0.
    Preserve the engine action verbatim; do not classify every action as speech."""
    if actor not in roster:
        return 0
    content = _strip_leading_name(actor, action)
    if not content:
        return 0
    emit(CustomEvent(kind="concordia.action", data={
        "step": step, "actor": actor, "content": action,
    }))
    return 1


# The allowlist of per-entity Concordia components whose values are semantically part
# of the experiment — what a researcher would cite, distinct from the llm.call process
# record (which stays complete but is not required reading). GM bookkeeping (terminate,
# next_acting, action specs, its repeated summaries) is deliberately absent: framework
# noise, auditable via llm.calls only.
_PERCEPTION_COMPONENTS = {
    "SelfPerception": "self",
    "SituationPerception": "situation",
    "ConversationDynamics": "dynamics",
}


class TurnEmitter:
    """`play()`'s step_callback: emit each resolved turn as a native action, drain the
    engine's raw_log into the semantic events (observations, per-turn perceptions),
    and count what landed. Concordia passes a StepData; only its (step, acting_entity,
    action) triple is read, so tests can feed any plain object. Pass `self.raw_log` to
    `play(raw_log=...)` and call `drain()` once after play returns.

    Semantic tier, per acting turn: a concordia.observation custom event for
    each observation newly written to that agent's memory (the raw_log lists a rolling
    window, so a per-agent high-water mark emits each observation exactly once), and
    one `concordia.perception` with the agent's current self/situation/
    dynamics readings (evolving state — each value emitted the turn it's computed)."""

    def __init__(self, roster: set[str]) -> None:
        self.roster = set(roster)
        self.steps = 0
        self.world_events = 0
        self.raw_log: list = []
        self._drained = 0
        self._obs_seen: dict[str, int] = {}

    def step(self, *args, **_kwargs) -> None:
        self.steps += 1
        step_data = args[0] if args else None
        if step_data is not None:
            self.world_events += emit_turn(
                getattr(step_data, "step", self.steps),
                getattr(step_data, "acting_entity", ""),
                getattr(step_data, "action", ""),
                self.roster,
            )
        self.drain()

    def drain(self) -> None:
        while self._drained < len(self.raw_log):
            self._emit_semantics(self.raw_log[self._drained])
            self._drained += 1

    def _emit_semantics(self, entry) -> None:
        step = entry.get("Step")
        emit(CustomEvent(kind="concordia.observations_recorded", data={"step": step}))
        for key, components in entry.items():
            if not (isinstance(components, dict)
                    and key.startswith("Entity [") and key.endswith("]")):
                continue
            agent = key[len("Entity ["):-1]

            # observations newly delivered to this agent (= its memory writes)
            window = (components.get("__observation__") or {}).get("Value") or []
            if isinstance(window, str):
                window = [window]
            seen = self._obs_seen.get(agent, 0)
            for text in window[seen:]:
                content = str(text).removeprefix("[observation]").strip()
                # `Event: ...` is event_resolution's record of the resolved public
                # turn, copied by the engine into every agent's memory — the same
                # action the step callback already retained as a native event.
                # Bookkeeping, not perception: only narrated observations are
                # semantic here.
                if content and not content.startswith("Event:"):
                    emit(CustomEvent(kind="concordia.observation", data={
                        "agent": agent, "text": content,
                    }))
            self._obs_seen[agent] = max(seen, len(window))

            # the agent's current read of itself and the scene, this turn (perception
            # components log their text under 'State'; memory-window ones under 'Value')
            perception = {
                field: str(value).strip()
                for comp, field in _PERCEPTION_COMPONENTS.items()
                if (value := (components.get(comp) or {}).get("State")
                    or (components.get(comp) or {}).get("Value"))
            }
            if perception:
                emit(
                    CustomEvent(
                        kind="concordia.perception",
                        data={"agent": agent, "step": step, **perception},
                    )
                )


def emit_summary(*, steps: int, agents: int,
                 world_events: int, model_calls: int) -> dict:
    """Emit the declared scalar results and return them as the summary
    dict matching the experiment's `results` schema."""
    summary = {
        "steps": steps,
        "agents": agents,
        "world_events": world_events,
        "model_calls": model_calls,
    }
    for name, value in summary.items():
        emit(Result(name=name, value=value))
    return summary
