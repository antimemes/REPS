# GovSim ("Cooperate or Collapse", Piatti et al., NeurIPS 2024) as a REPS
# experiment: 5 LLM personas share a common-pool resource — harvest, negotiate,
# collapse or sustain.
#
# Upstream is a package in the venv, built from the pinned checkout plus two
# patches: seed.patch changes existing behavior, packaging.patch only supplies
# missing packaging. Its pathfinder DSL is a uv git dependency. Other adaptation
# lives in ./govsim_adapter: Hydra composition, model injection, hash embeddings
# for keyless runs, wandb neutralization, and results from persisted native logs.
{ reps, pkgs, lib }:
let
  govsimUpstream = final: final.callPackage
    ({ stdenv, pyprojectHook, resolveBuildSystem }: stdenv.mkDerivation {
      pname = "govsim";
      version = "0-unstable-2025-01-19";
      src = builtins.fetchGit {
        url = "https://github.com/giorgiopiatti/GovSim";
        ref = "main";
        rev = "1d11adf047b24fa2ba0d44a1d4931015ea2e5210";
      };
      patches = [ ./seed.patch ./packaging.patch ];
      nativeBuildInputs = [ pyprojectHook ] ++ resolveBuildSystem { setuptools = [ ]; };
    }) { };

  overrides = final: prev:
    # Legacy setup.py packages need their undeclared build system supplied.
    lib.genAttrs [ "pathfinder" "antlr4-python3-runtime" ] (name:
      prev.${name}.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ])
          ++ final.resolveBuildSystem { setuptools = [ ]; };
      })) // { govsim = govsimUpstream final; };
  env = (reps.mkPythonEnv {
    name = "govsim-env";
    workspaceRoot = ./.;
    python = pkgs.python313;
    extraPackages = [ "govsim" ];
    inherit overrides;
  }).overrideAttrs { meta.mainProgram = "govsim"; };

  mockSuggestion = {
    value = "mock/model";
    description = "Keyless offline mock — scenario-aware deterministic replies, no network (the smoke/CI path).";
  };
in
{
  govsim = reps.mkExperiment {
    name = "govsim";
    schema = { version = 0; models = "govsim_adapter.models:Payload"; };
    schemaPython = "${env}/bin/python";
    summary = "GovSim (NeurIPS 2024): 5 LLM personas share a common-pool resource — harvest, negotiate, collapse or sustain.";
    # identity = declaration + locks + code; README/docs/default.nix stay out
    src = [ ./package.nix ./seed.patch ./packaging.patch ./pyproject.toml ./uv.lock ./govsim_adapter ];
    links = [
      { label = "paper"; url = "https://arxiv.org/abs/2404.16698"; }
      { label = "source"; url = "https://github.com/giorgiopiatti/GovSim"; }
    ];
    params = with reps.types; {
      experiment = param (enum [
        "fish_baseline_concurrent"
        "fish_baseline_concurrent_paraphrase_1"
        "fish_baseline_concurrent_paraphrase_2"
        "fish_baseline_concurrent_universalization"
        "fish_perturbation_no_language"
        "fish_perturbation_outsider"
        "fish_perturbation_outsider_universalization"
        "sheep_baseline_concurrent"
        "sheep_baseline_concurrent_universalization"
        "sheep_perturbation_no_language"
        "sheep_perturbation_outsider"
        "sheep_perturbation_outsider_universalization"
        "pollution_baseline_concurrent"
        "pollution_baseline_concurrent_universalization"
        "pollution_perturbation_no_language"
        "pollution_perturbation_outsider"
        "pollution_perturbation_outsider_universalization"
      ]) {
        description = "Scenario and treatment to run.";
        initial = "fish_baseline_concurrent";
        order = 1;
        group = "scenario";
      };
      max_rounds = param int {
        description = "Round (month) cap; 0 = the upstream per-experiment value (12 baseline, 15 outsider).";
        initial = 0;
        order = 2;
        group = "scenario";
      };
      model = param llm {
        description = "Model used by every agent. Use mock/model for an offline test.";
        initial = "mock/model";
        suggestions = [ mockSuggestion ];
        order = 1000;
        group = "model";
      };
      embedder = param (enum [ "hash" "mxbai" ]) {
        description = "Memory embeddings: hash gives artificial similarities for offline testing; mxbai uses the paper’s embedding model at the revision pinned in the adapter and may download about 1.3 GB.";
        initial = "hash";
        order = 1010;
        group = "model";
      };
      threads = param int {
        description = "CPU threads per process for PyTorch and numeric libraries.";
        initial = 2;
        order = 1100;
        group = "compute";
      };
      temperature = param float {
        nullable = true;
        description = "Sampling temperature; null omits it from requests (upstream default 0.0).";
        initial = 0.0;
        order = 1020;
        group = "generation";
      };
      top_p = param float {
        nullable = true;
        description = "Nucleus sampling top_p; null omits it from requests (upstream default 1.0).";
        initial = 1.0;
        order = 1021;
        group = "generation";
      };
      reasoning_effort = param (enum [ "low" "medium" "high" "xhigh" "max" ]) {
        description = "Reasoning effort for compatible models; null leaves the provider default. For Astra use low and set temperature/top_p to null.";
        nullable = true;
        initial = null;
        order = 1023;
        group = "generation";
      };
      max_tokens = param int {
        description = "Per-call completion budget (upstream Gen default 8000); enforced as a cap on every request.";
        initial = 8000;
        order = 1022;
        group = "generation";
      };
    };
    results = with reps.types; [
      {
        name = "rounds";
        type = int;
        label = "Rounds simulated";
        description = "Months reached by the simulation.";
        details = "Last recorded harvest round plus one, since round numbering starts at zero. Zero if no harvesting was recorded. Actual progress may be shorter than the configured run length.";
        unit = "months";
      }
      {
        name = "collapsed";
        type = bool;
        label = "Resource collapsed";
        description = "Whether the shared resource was recorded as depleted.";
        details = "Collapse means a round's recorded post-harvest resource pool fell below 5 or was missing (NaN). The calculation uses the last non-missing pool value in each round, or NaN if all are missing. No also covers runs with no recorded harvesting. Collapse is a simulation outcome, not an execution failure.";
      }
      {
        name = "survival_months";
        type = int;
        label = "Survival duration";
        description = "Month of collapse, or the configured run length if none was recorded.";
        details = "Uses the first collapse round plus one, or the configured run length if no collapse was recorded, following the paper's formula. This can exceed actual recorded progress. Zero if no harvesting was recorded.";
        unit = "months";
      }
      {
        name = "total_harvest";
        type = int;
        label = "Total resource collected";
        description = "Resource collected by all agents over the whole run.";
        details = "Sum of resource_collected across all logged harvest actions, agents and rounds. Resource meaning depends on the fish, sheep or pollution scenario.";
        unit = "resource units";
      }
      {
        name = "gain_per_agent";
        type = float;
        label = "Mean gain per agent";
        description = "Average resource collected per harvesting agent over the whole run.";
        details = "Average total resource collected per agent over the whole run, not per month. Includes only agents with recorded harvesting activity. This is the paper's total gain metric.";
        unit = "resource units per agent";
      }
      {
        name = "final_resource";
        type = int;
        label = "Final resource pool";
        description = "Resource left after the last recorded harvest.";
        details = "Uses the last non-missing post-harvest pool value in the final recorded harvest round, before any subsequent regeneration. Zero if no harvest actions were logged.";
        unit = "resource units";
      }
      {
        name = "equality";
        type = float;
        label = "Harvest equality";
        description = "How evenly agents shared the collected resource; higher is more equal.";
        details = "One minus the Gini coefficient of total resource collected per logged harvester; higher values mean more equal gains. All-zero gains score 1, so equality alone does not establish productive cooperation. No harvest actions yields 0.";
      }
      {
        name = "over_usage";
        type = float;
        label = "Requests above sustainable share";
        description = "Share of harvest requests above the estimated sustainable allowance.";
        details = "Fraction of harvest actions requesting more than (pre-harvest pool // 2) // distinct harvesters in that round; // is integer division. This adapter reconstruction counts requests, not allocations, and can differ from upstream's fixed agent count and reset-time threshold, especially when outsiders join. No harvest actions yields 0.";
      }
    ];
    env.network = true; # hosted models and optional HuggingFace embedder downloads
    program = env;
  };
}
