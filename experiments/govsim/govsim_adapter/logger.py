"""RepsLogger — upstream's WandbLogger, neutralized and translated to REPS events.

Subclass, not fork (extend-tool-as-library): ``debug=True`` is forced, so
``wandb.init(mode="disabled")`` makes every trace/log upstream fires a no-op —
no network, no files. On top of that:

* ``get_agent_chain`` maps names to configured persona IDs; ``start_chain``
  attributes framework calls to their query component and records phase/query
  metadata. One backend serves all personas, matching upstream's single-LLM shape.
* ``log_game`` (the per-step hook every scenario ``run()``/env already calls)
  accumulates the round's stats and emits one ``govsim.state`` event per round;
  its ``resource`` field is the chartable pool series.

Round boundaries are inferred from the stats stream itself: a
``conversation_resource_limit`` key closes the round (the restaurant phase), and
a repeated ``*_collected_resource`` key means a new round began — which covers
the no-language perturbation, whose phase cycle drops the restaurant step. The
pool reported for a round is the value observed alongside its last stats entry
(the post-harvest pool), not the post-doubling value the next round opens with.
"""

from __future__ import annotations

from reps_events import emit
from simulation.utils import WandbLogger
from .models import GovsimState, StateData

_COLLECTED = "_collected_resource"
_LIMIT = "conversation_resource_limit"
_FRAMEWORK_QUERIES = {
    "prompt_summarize_conversation_in_one_sentence": "summarize_conversation",
    "prompt_find_harvesting_limit_from_conversation": "find_harvesting_limit",
    "prompt_text_to_triple": "text_to_triple",
}


class RepsLogger(WandbLogger):
    def __init__(self, experiment_name: str, config: dict, *, backend) -> None:
        super().__init__(experiment_name, config, debug=True)
        self._backend = backend
        # Match upstream run.py's identity map: the logger receives display names,
        # while log_env.json and the model-call boundary use stable persona IDs.
        personas = config["experiment"]["personas"]
        self._agent_ids = {
            personas[f"persona_{i}"]["name"]: f"persona_{i}"
            for i in range(personas["num"])
        } | {"framework": "framework"}
        self._round = 0
        self._pool: int | None = None  # pool observed with the round's stats
        self._collected: dict[str, int] = {}
        self._limit: int | None = None

    # -- llm.call attribution -------------------------------------------------

    def get_agent_chain(self, agent_name, phase_name):
        self._agent_id = self._agent_ids[agent_name]
        self._backend.client.agent = self._agent_id
        return super().get_agent_chain(agent_name, phase_name)

    def start_chain(self, chain_name):
        # ModelWandbWrapper calls these two hooks in order, before any request.
        phase, query = chain_name.split("::", 1)
        self._backend.client.agent = (
            f"framework/{_FRAMEWORK_QUERIES[query]}"
            if self._agent_id == "framework" else self._agent_id
        )
        self._backend.client.metadata = {"govsim.phase": phase, "govsim.query": query}
        return super().start_chain(chain_name)

    # -- live progress --------------------------------------------------------

    def _flush(self, *, final: bool = False) -> None:
        emit(
            GovsimState(
                data=StateData(round=self._round, resource=self._pool,
                               collected=self._collected, limit=self._limit, final=final),
            )
        )
        self._round += 1
        self._collected = {}
        self._limit = None

    def log_game(self, kwargs, last_log=False):
        super().log_game(kwargs, last_log)
        stats = {k: v for k, v in kwargs.items() if k.endswith(_COLLECTED)}
        # a persona betting again = the next round started before this one was
        # closed by a restaurant phase — flush with the previous round's pool
        if any(key[: -len(_COLLECTED)] in self._collected for key in stats):
            self._flush()
        for key, value in stats.items():
            self._collected[key[: -len(_COLLECTED)]] = value
        # latch the pool alongside stats: the round's LAST stats-bearing step
        # observes the post-harvest pool (the survival-relevant series), never
        # the post-doubling value the next round opens with
        if stats or _LIMIT in kwargs:
            self._pool = kwargs.get("num_resource", self._pool)
        if _LIMIT in kwargs:
            self._limit = kwargs[_LIMIT]
            self._flush(final=last_log)
        elif last_log and self._collected:
            # A bare terminal notification after an already flushed round is
            # not another harvest. In particular, don't chart its regrown pool.
            self._flush(final=True)
