# AgentsNet

Can a network of LLM agents, each talking only to its neighbours, organise
itself to solve a problem that needs the whole network to agree?

**AgentsNet** ([Grötschla, Müller, Tönshoff, Galkin & Perozzi, 2025](https://arxiv.org/abs/2507.08616))
puts one agent on every node of a graph. Agents know only their own name and
their neighbours' names, exchange text messages in synchronous rounds, and at
the end each one answers for itself. The five tasks are classics of distributed
computing: graph coloring, maximal matching, minimal vertex cover, consensus and
leader election. A task is solved only if the agents' individual answers fit
together across the whole graph.

This experiment runs the **authors' own code** exactly as they released it
([GitHub](https://github.com/floriangroetschla/AgentsNet), MIT, pinned by revision
in `upstream.json`) on the **authors' own graph instances**
([Hugging Face `disco-eth/AgentsNet`](https://huggingface.co/datasets/disco-eth/AgentsNet),
MIT, pinned by content in `dataset.json`). A thin adapter routes every model
call through REPS so it is recorded, runs one cell of the authors' sweep per
run, and reads the score from the results file the authors' code writes.
Nothing in their code is edited; what the adapter replaces at import time is
listed in `agentsnet_adapter/patch.py`.

## One run

One REPS run is one task on one graph instance: what one iteration of the loop
in upstream's `main.py` does. The authors' `main.sh` runs sizes 4, 8 and 16 with
4, 5 and 6 rounds, three instances of each of three graph families, for each
of the five tasks: 135 runs per model.

| Parameter | Meaning |
| --- | --- |
| `task` | coloring, matching, vertex_cover, consensus or leader_election. |
| `graph_generator`, `graph_size`, `graph_index` | Which dataset instance: a family (ws = Watts-Strogatz, ba = Barabási-Albert, dt = Delaunay triangulation), a node count (4, 8, 16 for the benchmark; 20 to 100 in steps of 10 for the paper's scaling study) and an instance number (0 to 3 for the three small sizes, 0 to 2 above). |
| `rounds` | Message-passing rounds. Upstream ignores it for consensus and leader election and for graphs over 16 nodes, which get 2 x diameter + 1. |
| `chain_of_thought` | Whether agents are asked to reason step by step before each message and the final answer (on in the paper). |
| `model` | `provider/model` for every agent. |

Every agent is first told the rules, its name, its neighbours, the number of
rounds and the task, and asked for its first messages. In each round it receives
its neighbours' last messages and writes new ones as a JSON object keyed by
neighbour name. When the rounds are up it receives the last messages and answers
the task question in a fixed format. An answer or message that cannot be parsed
is asked for once more; a second failure counts as no message, or as an answer
of `None`.

## Reading the results

- **score** is the authors' task score, 0 to 1: the share of edges with
  differently coloured endpoints (coloring), the share of agents whose pairing
  is consistent (matching), edge coverage times the share of coordinators that
  cannot be removed (vertex cover), and 1 or 0 for consensus and leader
  election. Any answer outside the valid options makes coloring score 0 and
  consensus and leader election fail.
- **solved** is the paper's headline metric: a run counts only when the score
  is exactly 1, the whole network satisfying the task. Table 2 reports the
  fraction of solved instances; the soft score is the paper's Appendix B measure.
- **successful** is upstream's flag that message passing and scoring finished.
  An unsuccessful run has no score.
- **upstream_crashed** marks a vertex-cover run in which no agent answered Yes.
  Upstream's scorer divides by zero there and its process dies without a results
  file; its recovery tooling then reruns the instance, so the paper cannot count
  such runs. Here the run is kept with score 0, the score an empty cover earns.
  Drop flagged runs to approximate the authors' numbers; keep them to see what
  the models did.
- **rounds_run** is the round count upstream actually used.
- **fallbacks**, **unparsed_messages** and **unparsed_answers** are upstream's
  own counters of re-prompts and of agents whose output stayed unparseable. Read
  them before comparing models: upstream's message parser rewrites `\n` escapes
  inside the JSON it receives into literal newlines, which makes valid JSON
  invalid, so models that write multi-line messages are re-prompted and often
  silenced for the round through no fault of their own.
- **messages_sent** counts the messages that reached a neighbour, reconstructed
  from the transcripts.

Every run records each model call (`llm.call`, attributed to the agent's name),
the graph (`agentsnet.graph`), each message with its round, sender and
recipient (`agentsnet.message`), each agent's final answer (`agentsnet.answer`),
the settings it ran with (`agentsnet.config`), and the authors' results JSON as
an artifact with its hash (`agentsnet.upstream_record`). The results file holds
the full per-agent transcripts in LangChain's message format; upstream's
`chat_tool.py` reads it.

## Run settings

- **Quick check:** `mock/model` on a 4-node graph with 2 rounds. The mock
  messages every neighbour each round and picks a final answer from the valid
  options by seed; it exercises the loop, parsing and scoring and says nothing
  about any model.
- **The paper's grid:** sizes 4, 8, 16 with rounds 4, 5, 6, instances 0 to 2,
  all three families, all five tasks. The paper's models are listed in
  upstream's `main.py`; here any `provider/model` the provider serves can be
  named.
- **Scaling study:** sizes 20 to 100 (upstream `ablations.sh`); rounds are
  2 x diameter + 1 regardless of the parameter. Every agent's conversation is
  replayed on every call, so cost grows with rounds squared and with the node
  count.
- Upstream paces its API calls with a per-model rate-limiter table; pacing
  never changes a reply, so no throttling is applied here and provider 429s are
  retried with back-off by the REPS client.

## Scope

The graphs are the authors' published instances; no graphs are generated here
(upstream's `generate_graphs.py` is in the tree but not called). The model's
request parameters are what upstream's LangChain classes would have sent at the
pinned versions, with two provider-specific defaults reproduced
(temperature 0.7 for Google, 1024 max tokens for Anthropic); the thinking variants of Claude and Gemini that upstream
names cannot be requested through REPS's OpenAI-protocol client and are not
offered. Upstream's `recovery mode` (rerunning missing rows of a CSV) is a sweep
tool and has no counterpart in a single run.
