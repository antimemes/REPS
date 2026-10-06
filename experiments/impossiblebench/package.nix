# ImpossibleBench (arXiv:2510.20270) as REPS experiments: coding benchmarks whose
# "impossible" test variants (oneoff / conflicting) can only be passed by
# specification-violating shortcuts — passing IS the reward-hacking signal.
#
# One experiment per upstream @task (the task is the experiment, never a param):
#   impossiblebench-livecodebench → impossiblebench.impossible_livecodebench
#   impossiblebench-swebench      → impossiblebench.impossible_swebench
#
# Both run agentic coding loops inside an inspect sandbox: docker must be on the
# host PATH (SWE-bench pulls per-instance images; the runner forwards PATH into the
# experiment). VM-isolated execution (forest.nix) is the planned replacement.
{ reps, pkgs, lib, writeShellApplication, jq }:

let
  env = reps.mkPythonEnv {
    name = "reps-impossiblebench-env";
    workspaceRoot = ./.;
    python = pkgs.python313;
    # the two git deps build from source (no wheels) and both use setuptools —
    # impossiblebench ships a legacy setup.py, inspect-evals declares the backend
    # without it being provided — so inject setuptools as their build system
    overrides = final: prev:
      lib.genAttrs [ "impossiblebench" "inspect-evals" ] (name:
        prev.${name}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ])
            ++ final.resolveBuildSystem { setuptools = [ ]; };
        }));
  };

  results = with reps.types; [
    { name = "samples"; type = int; }
    { name = "completed"; type = int; }
    { name = "errors"; type = int; }
    { name = "score"; type = float; }
    { name = "score_name"; type = str; }
    { name = "tokens_input"; type = int; }
    { name = "tokens_output"; type = int; }
  ];

  # hints come from types.llm itself (mkExperiment attaches the shared model
  # catalog to every llm-typed param); nothing to declare here
  modelParam = with reps.types; param llm {
    description = "Inspect model id (provider/model), resolved through inspect's own provider layer. Required: there is deliberately no default real model.";
    order = 1000;
    group = "model";
  };

  splitParam = with reps.types; param (enum [ "original" "oneoff" "conflicting" ]) {
    description = "Dataset split: `original` = the unmutated benchmark (the control); `oneoff` / `conflicting` = impossible variants where passing implies test exploitation. Required: the split is the condition's whole point.";
    order = 1;
    group = "task";
  };

  sharedParams = with reps.types; {
    limit = param int {
      description = "Sample cap (0 = the whole split).";
      initial = 0;
      order = 1010;
      group = "inspect";
    };
    epochs = param int {
      description = "Repeats of the dataset (1 = a single pass).";
      initial = 1;
      order = 1011;
      group = "inspect";
    };
    generate_args = param object {
      description = "Generation config overrides; leave a field unset to keep the provider's default.";
      initial = { };
      # typed sub-form generated from the pinned inspect_ai's GenerateConfig —
      # through `reps`, never a ../../ path, so the declaration uses the
      # schema provided by the selected reps package set
      fields = reps.inspectGenerateFields;
      order = 1020;
      group = "generation";
    };
  };

  # the lift between the runner protocol (flat params on stdin, seed in $REPS_SEED)
  # and the wrapper's config: named params regroup into inspect task_args; the task
  # spec is baked per experiment.
  mkAdapter = name: taskFn: jqTaskArgs: writeShellApplication {
    name = "${name}-adapter";
    runtimeInputs = [ jq ];
    # limit goes to the task too, not just eval-level: inspect's limit truncates
    # after the task fn has already built its dataset, and swebench prepares a
    # docker image per sample in there — eval-level alone means images for the
    # whole split. 0 (= whole split) must stay absent: upstream slices
    # samples[:limit] whenever its arg is non-null.
    text = ''
      config=$(mktemp)
      trap 'rm -f "$config"' EXIT
      jq --argjson seed "''${REPS_SEED:-0}" '{
        task: "pkg:impossiblebench:${taskFn}",
        model: .model,
        limit: .limit,
        epochs: .epochs,
        generate_args: .generate_args,
        seed: $seed,
        task_args: (${jqTaskArgs}
          + (if .limit > 0 then {limit: .limit} else {} end))
      }' > "$config"
      ${lib.getExe' env "reps-inspect-eval"} "$config"
    '';
  };

  # hand-written (no upstream link registry here). Links are pointers, not pins:
  # the exact rev lives in pyproject.toml/uv.lock
  links = [
    { label = "paper"; url = "https://arxiv.org/abs/2510.20270"; }
    { label = "source"; url = "https://github.com/safety-research/impossiblebench"; }
  ];
in
{
  impossiblebench-livecodebench = reps.mkExperiment {
    schemaPython = "${env}/bin/python";
    sharedSrcs = reps.inspectSharedSrcs;
    name = "impossiblebench-livecodebench";
    src = ./.;
    summary = "ImpossibleBench LiveCodeBench: function-implementation tasks with impossible test variants — passing an impossible split means the agent gamed the tests";
    inherit links;

    params = with reps.types; {
      model = modelParam;
      split = splitParam;
      agent_type = param (enum [ "minimal" "tools" ]) {
        description = "Agent scaffold: `minimal` = submission loop with test feedback; `tools` = SWE-style file editing with bash.";
        initial = "minimal";
        order = 2;
        group = "task";
      };
      max_attempts = param int {
        description = "Maximum submission attempts per sample.";
        initial = 3;
        order = 3;
        group = "task";
      };
      message_limit = param int {
        description = "Per-sample message cap.";
        initial = 30;
        order = 4;
        group = "task";
      };
      allow_test_modifications = param bool {
        description = "Whether the agent may edit test files (test modification is one of the cheating channels the benchmark measures).";
        initial = true;
        order = 5;
        group = "task";
      };
    } // sharedParams;

    inherit results;
    env = { network = true; docker = true; };
    program = mkAdapter "impossiblebench-livecodebench" "impossible_livecodebench"
      ''{split: .split, agent_type: .agent_type, max_attempts: .max_attempts,
         message_limit: .message_limit, allow_test_modifications: .allow_test_modifications}'';
  };

  impossiblebench-swebench = reps.mkExperiment {
    schemaPython = "${env}/bin/python";
    sharedSrcs = reps.inspectSharedSrcs;
    name = "impossiblebench-swebench";
    src = ./.;
    summary = "ImpossibleBench SWE-bench: real-repo issue fixing with impossible test variants — passing an impossible split means the agent gamed the tests";
    inherit links;

    params = with reps.types; {
      model = modelParam;
      split = splitParam;
      agent_type = param (enum [ "minimal" "tools" ]) {
        description = "Agent scaffold: `minimal` = mini-agent (bash only); `tools` = multi-submission with bash, python, and a text editor.";
        initial = "tools";
        order = 2;
        group = "task";
      };
      max_attempts = param int {
        description = "Maximum submission attempts per sample.";
        initial = 10;
        order = 3;
        group = "task";
      };
      message_limit = param int {
        description = "Per-sample message cap.";
        initial = 100;
        order = 4;
        group = "task";
      };
      allow_internet = param bool {
        description = "Whether the sandbox may reach the internet.";
        initial = false;
        order = 5;
        group = "task";
      };
    } // sharedParams;

    inherit results;
    env = { network = true; docker = true; };
    program = mkAdapter "impossiblebench-swebench" "impossible_swebench"
      ''{split: .split, agent_type: .agent_type, max_attempts: .max_attempts,
         message_limit: .message_limit, allow_internet: .allow_internet}'';
  };
}
