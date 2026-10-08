# AgentsNet ("Coordination and Collaborative Reasoning in Multi-Agent LLMs",
# Grötschla, Müller, Tönshoff, Galkin & Perozzi, arXiv:2507.08616) as a REPS
# experiment: the authors' released code (github.com/floriangroetschla/AgentsNet,
# MIT, pinned by revision in upstream.json) copied verbatim into each run and
# driven by a thin adapter in ./agentsnet_adapter, the way prompt-infection wraps
# its authors' tree. One run is one cell of the authors' sweep: one task on one
# graph instance from their published dataset (Hugging Face disco-eth/AgentsNet,
# pinned by content in dataset.json), with every node's model calls routed
# through REPS and recorded.
#
# Upstream is not patched on disk. The adapter swaps the LangChain model
# construction for a REPS-instrumented chat model, points the dataset loader at
# the pinned file, and reads the authors' own results JSON. patch.py lists
# every touch.
{ reps, pkgs, lib }:
let
  # The authors' code, pinned by revision. upstream.json stays out of src so
  # moving the mirror does not change condition identity; the fetched tree is in
  # src, so a new revision does.
  pin = builtins.fromJSON (builtins.readFile ./upstream.json);
  upstream = builtins.fetchGit { inherit (pin) url ref rev; };
  # The graph instances, one parquet file fetched by content hash. Same rule:
  # the sidecar is outside src, the file is inside.
  datasetPin = builtins.fromJSON (builtins.readFile ./dataset.json);
  dataset = builtins.fetchurl { inherit (datasetPin) url sha256; };

  env = reps.mkPythonEnv {
    name = "agentsnet-env";
    workspaceRoot = ./.;
    python = pkgs.python313;
  };
  # the launcher names the authors' tree, its revision and the dataset file
  # (upstream.py reads them); a developer points the same variables at a
  # checkout and a downloaded parquet
  program = pkgs.runCommand "agentsnet-launcher" {
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta.mainProgram = "agentsnet";
  } ''
    mkdir -p $out/bin
    makeWrapper ${env}/bin/agentsnet $out/bin/agentsnet \
      --set AGENTSNET_UPSTREAM ${upstream} \
      --set AGENTSNET_UPSTREAM_REV ${pin.rev} \
      --set AGENTSNET_DATASET ${dataset}
  '' // {
    # the pytest tester selects the venv's dev group through program.override, and
    # tests/default.nix reads the tree and dataset the launcher points at
    override = env.override;
    inherit upstream dataset;
    upstreamRev = pin.rev;
  };

  mockSuggestion = {
    value = "mock/model";
    description = "Keyless offline mock: every agent messages all its neighbours each round and picks a final answer deterministically from the valid options, so a run completes end to end without a key (the smoke/CI path). Says nothing about a real model.";
  };
in
{
  agentsnet = reps.mkExperiment {
    name = "agentsnet";
    # identity = declaration + locks + adapter + the authors' tree + the graphs; README/docs/sidecars stay out
    src = [ ./package.nix ./pyproject.toml ./uv.lock ./agentsnet_adapter upstream dataset ];
    schema = { version = 0; models = "agentsnet_adapter.models:Payload"; };
    schemaPython = "${env}/bin/python";
    summary = "AgentsNet (Grötschla et al. 2025), the authors' code: a network of LLM agents, one per graph node, solves a distributed-computing problem (coloring, matching, vertex cover, consensus or leader election) by synchronous message passing with its neighbours.";
    links = [
      { label = "paper"; url = "https://arxiv.org/abs/2507.08616"; }
      { label = "source"; url = "https://github.com/floriangroetschla/AgentsNet"; }
      { label = "dataset"; url = "https://huggingface.co/datasets/disco-eth/AgentsNet"; }
    ];
    params = with reps.types; {
      task = param (enum [ "coloring" "consensus" "leader_election" "matching" "vertex_cover" ]) {
        description = "The distributed problem the agents solve (upstream --task). Coloring: neighbours end in different groups (max degree + 1 groups). Matching: pairs of neighbours name each other. Vertex cover: a minimal set of coordinators touching every edge. Consensus: all agree on 0 or 1. Leader election: exactly one agent says it is the leader.";
        initial = "coloring";
        order = 1;
        group = "cell";
      };
      graph_generator = param (enum [ "ws" "ba" "dt" ]) {
        description = "Random graph family of the instance (upstream --graph_models): ws = connected Watts-Strogatz (k=4, p=0.4), ba = Barabási-Albert (m=2), dt = Delaunay triangulation of random points.";
        initial = "ws";
        order = 2;
        group = "cell";
      };
      graph_size = param int {
        description = "Nodes in the graph, one agent each (upstream --graph_size). The dataset has 4, 8 and 16 (the paper's benchmark) and 20, 30, ..., 100 (its scaling study). Any other size is not in the dataset and the run fails before any model call.";
        initial = 4;
        order = 3;
        group = "cell";
      };
      graph_index = param int {
        description = "Which instance of that family and size: 0 to 3 for sizes 4, 8 and 16, 0 to 2 above. The authors' sweeps used 0 to 2.";
        initial = 0;
        order = 4;
        group = "cell";
      };
      rounds = param int {
        description = "Message-passing rounds before the final answer (upstream --rounds). Upstream ignores it for consensus and leader election and for graphs over 16 nodes, which run 2 x diameter + 1 rounds; the rounds actually run are the rounds_run result. The authors used 4, 5 and 6 for 4, 8 and 16 nodes.";
        initial = 4;
        order = 5;
        group = "cell";
      };
      chain_of_thought = param bool {
        description = "Ask each agent to reason step by step before its messages and its final answer (upstream default; --disable_chain_of_thought turns it off).";
        initial = true;
        order = 6;
        group = "cell";
      };
      model = param llm {
        description = "provider/model for every agent. Upstream's own model table is bypassed: any model the provider serves can be named. mock/model runs keyless.";
        initial = "openai/gpt-4o-mini";
        suggestions = [ mockSuggestion ]; # the shared model catalog supplies the rest
        order = 1000;
        group = "model";
      };
    };
    results = with reps.types; [
      {
        name = "score";
        type = float;
        label = "Task score";
        description = "The authors' score for the agents' final answers, 0 to 1.";
        details = "Upstream get_score of the task class. Coloring: share of edges whose endpoints chose different groups, 0 if any answer is not a group. Matching: share of agents whose answer is consistent (named a neighbour who named them back, or None with no neighbour also answering None). Vertex cover: edge coverage times the share of coordinators that are necessary; 0 when no agent answered Yes (upstream raises there; see upstream_crashed). Consensus and leader election: 1 or 0. Absent when the run was not successful (upstream records score null).";
      }
      {
        name = "solved";
        type = bool;
        label = "Task solved";
        description = "Whether the whole network satisfied the task: the paper's binary metric.";
        details = "True when the authors' score equals 1.0. Table 2 of the paper reports the fraction of solved instances; score itself is the paper's soft score (Appendix B). Absent when the run was not successful.";
      }
      {
        name = "successful";
        type = bool;
        label = "Run completed";
        description = "Whether message passing and scoring finished without one of the errors upstream catches.";
        details = "Upstream's successful flag: false when pass_messages or get_score raised ValueError or KeyError (for example an answer that could not be parsed where the scorer needs one); the error text is in the authors' record. Other exceptions are not caught by upstream: the empty-vertex-cover ZeroDivisionError is recorded with upstream_crashed (successful stays true); any other fails the run.";
      }
      {
        name = "upstream_crashed";
        type = bool;
        label = "Upstream crashed";
        description = "Whether upstream's vertex-cover scorer divided by zero because no agent answered Yes; the run is recorded with score 0.";
        details = "Upstream does not catch this ZeroDivisionError: its process dies and writes no results file, and its recovery tooling (--start_from_sample, --missing_run_file) reruns the instance, so the paper's numbers cannot include such runs. The adapter records score 0 (coverage 0 times an undefined minimality share; an empty cover covers no edge) and sets this flag. Drop runs with this flag to approximate the authors' procedure; keep them to count what the models did. Always false for other tasks.";
      }
      {
        name = "rounds_run";
        type = int;
        label = "Rounds run";
        description = "Message-passing rounds actually run.";
        details = "Upstream determine_rounds: the rounds parameter, or 2 x diameter + 1 for consensus, leader election and graphs over 16 nodes.";
        unit = "rounds";
      }
      {
        name = "messages_sent";
        type = int;
        label = "Messages delivered";
        description = "Messages addressed to a neighbour over all rounds, as reconstructed from the transcripts.";
        details = "Count of (round, sender, recipient) entries in agents' parsed JSON outputs where the recipient is a neighbour; upstream delivers only those. Messages to non-neighbours are recorded as undelivered agentsnet.message events and not counted. Each round's parsed output is the one upstream used, after any re-prompt.";
        unit = "messages";
      }
      {
        name = "fallbacks";
        type = int;
        label = "Re-prompts";
        description = "Times an agent was asked again because its output could not be parsed.";
        details = "Sum over agents of upstream's num_fallbacks: one per round in which the JSON messages, or the final answer, could not be parsed from the first reply.";
      }
      {
        name = "unparsed_messages";
        type = int;
        label = "Rounds with no parseable messages";
        description = "Agent-rounds in which the re-prompt also failed to yield JSON messages; that agent sent nothing that round.";
        details = "Sum over agents of upstream's num_failed_json_parsings_after_retry.";
      }
      {
        name = "unparsed_answers";
        type = int;
        label = "Agents with no parseable final answer";
        description = "Agents whose final answer could not be parsed even after the re-prompt; their answer is None in scoring.";
        details = "Sum over agents of upstream's num_failed_answer_parsings_after_retry.";
      }
    ];
    env.network = true; # hosted models
    inherit program;
  };
}
