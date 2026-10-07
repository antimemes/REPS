"""agentsnet: the authors' code for "AgentsNet: Coordination and Collaborative
Reasoning in Multi-Agent LLMs" (Grötschla et al., arXiv:2507.08616), run as a
REPS experiment.

The code is the authors' public repository, pinned by revision in upstream.json
and copied into each run directory. One REPS run is one cell of the authors'
sweep (`main.py --task T --graph_size N` loops graph families and instances;
`main.sh` loops sizes and tasks): one task on one graph instance from their
published dataset. The adapter stages the tree, loads the instance with
upstream's own `main.get_graph` (pointed at the pinned parquet), fixes the round
count with upstream's `determine_rounds`, swaps the model construction for a
REPS-instrumented chat model (patch.py, llm.py), drives the task class exactly
as upstream's `run()` does (bootstrap, pass_messages, get_score, with the same
error handling), has upstream write its results JSON with `save_results`,
ingests that file, and reports the paper's score and upstream's parsing counters
from it. Upstream's prompts, parsing, scoring and bugs are untouched; patch.py
lists what the adapter replaces.

The program speaks the runner protocol (reps-experiment packages it): params
arrive as JSON on stdin (or a config path on argv for hand-runs), events leave
over the event socket. Any params or results change here must be mirrored in
package.nix.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from reps_events import Result, Status, emit
from reps_experiment.scaffold import experiment_main

from . import llm, patch, results, upstream
from .mock import mock_responder
from .models import (AgentsNetAnswer, AgentsNetConfig, AgentsNetGraph, AgentsNetMessage, AnswerData, ConfigData,
                     GraphData, GraphEdge, GraphNode, MessageData)

Task = Literal["coloring", "consensus", "leader_election", "matching", "vertex_cover"]
Generator = Literal["ws", "ba", "dt"]


class Params(BaseModel):
    """One cell of upstream main.py's loops: one task, one dataset instance."""

    model: str                                  # provider/model for every agent
    task: Task
    graph_generator: Generator                  # upstream --graph_models, one of them
    graph_size: int = Field(ge=1)               # upstream --graph_size; must exist in the dataset
    graph_index: int = Field(ge=0)              # the instance (upstream loops start_from_sample..samples_per_graph_model)
    rounds: int = Field(ge=1)                   # upstream --rounds (overridden by determine_rounds for some cells)
    chain_of_thought: bool                      # upstream: not --disable_chain_of_thought


def _seed() -> int:
    return int(os.environ.get("REPS_SEED", "0")) & 0x7FFFFFFF


def _graph_data(graph, params: Params, graph_seed: int | None) -> GraphData:
    import networkx as nx

    return GraphData(
        generator=params.graph_generator, index=params.graph_index,
        num_nodes=graph.number_of_nodes(), num_edges=graph.number_of_edges(),
        diameter=nx.diameter(graph), max_degree=max(dict(graph.degree()).values()),
        graph_seed=graph_seed,
        nodes=[GraphNode(id=int(v), name=str(graph.nodes[v]["name"])) for v in graph.nodes],
        edges=[GraphEdge(source=int(u), target=int(v)) for u, v in graph.edges],
    )


def _dataset_graph_seed(params: Params) -> int | None:
    """The generator seed the dataset row records beside the graph (metadata
    upstream's get_graph drops)."""
    import json

    import pandas as pd

    df = pd.read_parquet(upstream.dataset_path())
    row = df[(df["graph_generator"] == params.graph_generator) & (df["num_nodes"] == params.graph_size)
             & (df["index"] == params.graph_index)]
    if len(row) == 0:
        return None
    seed = json.loads(row.iloc[0]["graph"]).get("graph_seed")
    return int(seed) if seed is not None else None


async def _drive(model):
    """Upstream run()'s inner loop body for one instance, with its error handling."""
    await model.bootstrap()
    try:
        answers = await model.pass_messages()
        score = model.get_score(answers)
        return answers, score, True, None
    except (ValueError, KeyError) as e:
        return [None for _ in range(model.graph.order())], None, False, repr(e)


def run(params: Params) -> None:
    seed = _seed()
    run_dir = Path(os.environ.get("REPS_RUN_DIR", "."))
    run_dir.mkdir(parents=True, exist_ok=True)
    llm.reset_models()
    work = upstream.stage(run_dir)
    up_main = importlib.import_module("main")
    lmp = importlib.import_module("LiteralMessagePassing")

    # the instance, selected by upstream's own code from the pinned dataset;
    # a size or index that is not in the dataset raises here, before any model call
    patch.apply_dataset()
    graph = up_main.get_graph(params.graph_generator, params.graph_size, params.graph_index)
    rounds = int(up_main.determine_rounds(params.task, graph, params.graph_index, 0, params.rounds))
    agent_names = {str(v): str(graph.nodes[v]["name"]) for v in graph.nodes}

    provider, _, served = params.model.partition("/")
    responder = mock_responder(seed) if provider == "mock" else None
    patch.apply(params.model, seed, agent_names, mock_responder=responder)

    task_class = up_main.TASKS[params.task]
    emit(AgentsNetConfig(data=ConfigData(
        params=params.model_dump(), seed=seed, provider=provider, served_model=served, rounds=rounds,
        upstream_rev=upstream.upstream_rev(), upstream_class=f"LiteralMessagePassing.{task_class.__name__}",
        dataset_sha256=upstream.sha256_of(upstream.dataset_path()))))
    emit(AgentsNetGraph(data=_graph_data(graph, params, _dataset_graph_seed(params))))
    emit(Status(detail=f"upstream {task_class.__name__} on {params.graph_generator} graph "
                       f"{params.graph_size}/{params.graph_index}: {rounds} rounds, {graph.number_of_nodes()} agents"))

    # upstream run(): task_class(graph, rounds, model_name, model_provider, chain_of_thought)
    model = task_class(graph=graph, rounds=rounds, model_name=served, model_provider=provider,
                       chain_of_thought=params.chain_of_thought)
    answers, score, successful, error_message = asyncio.run(_drive(model))

    # upstream's own record, then its ingestion
    up_main.save_results(
        answers=answers, transcripts=model.get_transcripts(), graph=model.graph, rounds=rounds,
        model_name=model.model_name, task=params.task, score=score,
        commit_hash=upstream.upstream_rev() or "None",     # upstream's literal when not in a git checkout
        graph_generator=params.graph_generator, graph_index=params.graph_index,
        successful=successful, error_message=error_message, chain_of_thought=params.chain_of_thought,
        num_fallbacks=model.num_fallbacks,
        num_failed_json_parsings_after_retry=model.num_failed_json_parsings_after_retry,
        num_failed_answer_parsings_after_retry=model.num_failed_answer_parsings_after_retry)
    record = upstream.ingest_record(work)

    delivered = 0
    for node in graph.nodes:
        name = agent_names[str(node)]
        neighbours = {agent_names[str(n)] for n in graph.neighbors(node)}
        for index, outgoing in enumerate(results.round_messages(model.chat_history[node], lmp.parse_messages)):
            for recipient, text in outgoing.items():
                is_neighbour = recipient in neighbours
                delivered += is_neighbour
                emit(AgentsNetMessage(data=MessageData(round=index + 1, sender=name, recipient=recipient,
                                                        text=text, delivered=is_neighbour)))
        answer = answers[node] if node < len(answers) else None
        emit(AgentsNetAnswer(data=AnswerData(agent=name, answer=answer,
                                             valid=answer in model.get_valid_answers(node))))

    values = results.summary(record, model, rounds)
    values["messages_sent"] = delivered
    for name, value in values.items():
        emit(Result(name=name, value=value))


def main() -> int:
    return experiment_main(Params, run, prog="agentsnet")
