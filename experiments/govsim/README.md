# GovSim

Can AI agents share a resource without exhausting it?

Five agents benefit from taking a shared resource, but overuse threatens
everyone’s future. GovSim tests whether they can agree on limits and follow them.

Based on [Cooperate or Collapse](https://arxiv.org/abs/2404.16698)
(Piatti et al., NeurIPS 2024). [Original code](https://github.com/giorgiopiatti/GovSim).

## How it works

Each round represents a month: agents harvest, observe each other’s actions,
discuss limits, and reflect. The resource can recover between rounds if enough
remains. Memories of earlier actions and conversations inform later decisions.

## Scenarios and conditions

The **scenario** changes the agents’ roles, decisions, and shared resource:

| Scenario | What each agent decides each month | Shared resource |
| --- | --- | --- |
| Fishing (fishery) | A fisher chooses how many tons of fish to catch. | A lake holding up to 100 tons of fish; each ton caught removes one ton. |
| Sheep herding (pasture) | A shepherd chooses how many sheep to graze. | Up to 100 hectares of grass; each sheep consumes one hectare. |
| Pollution | A factory owner chooses how many pallets of widgets to produce. | A river with up to 100% clean water; each pallet pollutes one percentage point. |

All three use the **same resource dynamics**: after each month, the remaining
fish, grass, or clean water doubles, up to its starting capacity. At full capacity,
the group can use 50 units per month without reducing next month’s supply—10 each
for five agents. The scenarios test whether the same cooperation problem elicits
different behavior when framed as extraction, grazing, or pollution
([paper, §§2.2–2.3](https://arxiv.org/html/2404.16698v4#S2.SS2)).

The **treatment** changes the agents’ instructions, information, or group membership:

| Treatment | What changes from baseline | What it tests |
| --- | --- | --- |
| Baseline | Five agents choose their own harvests, see others’ harvests, and discuss limits. | Can cooperation emerge? |
| Universalization | Adds a reminder that the resource shrinks if everyone exceeds the sustainable share. | Does guidance about collective consequences improve cooperation? |
| No discussion | Removes group conversation and observations of others’ harvests. | Can cooperation persist with less social information? |
| Outsider | Starts with four community-minded agents; a selfish newcomer joins after three months. | Can the group withstand a newcomer who disregards its norms? |

Each **recorded condition** also fixes the model and run settings.

### Additional variants in the upstream code

**Paraphrase 1 and 2** change the wording of the fishing baseline’s instructions,
while retaining its rules. They are optional upstream variants, not listed in
the authors’ [table of paper experiments](https://github.com/giorgiopiatti/GovSim#table-of-experiments).
Their configurations select the
[alternative fishing prompts](https://github.com/giorgiopiatti/GovSim/blob/1d11adf047b24fa2ba0d44a1d4931015ea2e5210/simulation/scenarios/fishing/agents/persona_v3/cognition/utils.py).
The code also supports combining an outsider with universalization.

## Run settings

- **Quick demonstration:** one round with `mock/model` and the `hash` embedder.
  These use scripted replies and artificial memory similarities.
- **Model behavior:** use a real model and `mxbai` for memory retrieval.
- **Full scenario:** set `max_rounds = 0`; normally 12 months, or 15 with outsiders.

**Resource collapse is an outcome, not an execution failure.** A short run
without collapse does not demonstrate lasting cooperation. Resource units differ
across scenarios; cumulative harvest also depends on run length.

### Reproducibility and recorded data

These runs are not automatically reproductions of the paper. Inspect each
recorded condition for its model, generation settings, embedder, and seeds.
The `mxbai` model revision is pinned in the adapter. A seed patch makes resource
allocation repeatable for the same seed and actions; hosted-model replies may
still vary.

`over_usage` measures requests above an agent’s sustainable share. Its calculation
uses the current number of harvesters, which differs from the upstream
simulation’s fixed count when an outsider joins.

Model calls and resource updates appear during execution; conversations and
memories are imported afterward. Imported timestamps record ingestion time,
not when agents acted. The Mayor’s messages are templated, with no model call.

The runner saves parameters, seeds, source references, and build information in
`run.json` and `run.start`. GovSim records its resolved configuration in
`govsim.config`, resource history in `govsim.state.resource`, and simulation rows
as `govsim.harvest`, `govsim.utterance`, `govsim.summary`, or
`govsim.resource_limit`. Other actions remain `govsim.record`.

Each upstream log has a `govsim.upstream_log` marker with its path, byte count,
SHA-256 hash, and ingested record count. Memory nodes become `govsim.memory`;
embeddings are not imported. Original fields and interaction HTML are retained
on disk; the viewer displays plain text. Available checkpoints are retained on
failure, with unreadable or missing logs reported as `govsim.unparsed_log`.
