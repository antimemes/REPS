# The inspect_evals family: one REPS experiment per runnable task in the GENERATED
# task_catalog.json (the task is the experiment, never a param) — regenerate with
# `task tasks:update`; never add a catalog task by hand. Tasks whose eval declares a
# pip extra we don't install get no experiment (degraded but correct — install the
# extra in pyproject + relock + regen to enable them); sandbox needs come from the
# eval's own declared runtime metadata. The bundled keyless `hello` smoke ships from
# this family (hello_task/), so getting-started needs no key, network, or sandbox.
{ reps, pkgs, lib, writeShellApplication, jq }:

let
  env = reps.mkPythonEnv {
    name = "reps-inspect-evals-env";
    workspaceRoot = ./.;
    python = pkgs.python313;
  };

  catalog = builtins.fromJSON (builtins.readFile ./task_catalog.json);
  # the typed sub-form schema for generate_args, generated from the pinned
  # inspect_ai's GenerateConfig (shared infra, like the model catalog) —
  # through `reps`, never a ../../ path, so the declaration uses the
  # schema provided by the selected reps package set
  genFields = reps.inspectGenerateFields;

  # task = the wrapper's task spec: `inspect_evals/<id>` resolves through inspect's
  # registry (the upstream package registers its tasks); `pkg:<module>:<attr>`
  # imports a @task callable from this family's env.
  tasks = lib.mapAttrs'
    (tname: t: lib.nameValuePair
      ("inspect-" + lib.replaceStrings [ "_" ] [ "-" ] tname)
      {
        inherit (t) task summary sandbox links;
        taskParams = t.params;
        paramKwargs = t.param_kwargs;
      })
    (lib.filterAttrs (_: t: t.dependency == null) catalog.tasks)
  // {
    inspect-hello = {
      task = "pkg:hello_task:hello";
      summary = "Say hello to your model: two instruction-following samples, scored in seconds — also runs keyless against inspect's mock";
      keyless = true;
    };
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

  # Experiment-specific EXTRA hints only — the shared model catalog is attached by
  # types.llm itself (mkExperiment), with extras prepended. The keyless mock leads:
  # it's this family's getting-started story and works for every inspect task.
  modelSuggestions = [
    { value = "mockllm/model"; description = "Inspect's built-in mock — keyless, offline, deterministic."; }
  ];

  mkTask = name: { task, summary, keyless ? false, sandbox ? false, taskParams ? { }, paramKwargs ? { }, links ? [ ] }: reps.mkExperiment {
    schemaPython = "${env}/bin/python";
    sharedSrcs = reps.inspectSharedSrcs;
    inherit name summary links;
    # The declaration and environment pin define this family's source identity.
    # task_catalog.json is not included; catalog regeneration alone leaves it unchanged.
    # Source identity does not establish comparability (docs/book/src/running/model.md).
    src = [ ./package.nix ./pyproject.toml ./uv.lock ]
      ++ lib.optional keyless ./hello_task;

    # presentation order (`order`/`group` hints): the task's own params first —
    # they're what a researcher cares about — then model, then the inspect harness
    # knobs, then generation.
    params = with reps.types;
      # the task's own kwargs, flattened to real typed params (catalog-generated:
      # concrete defaults become `initial`s; declared-None defaults become required
      # nullable params bound with `--set k=null`). Their `order` is the upstream
      # signature's declared order — never alphabetized.
      lib.mapAttrs (_: p: p // { group = "task"; }) taskParams
      // {
        model = param llm ({
          description =
            if keyless
            then "Inspect model id (provider/model). The default mock runs keyless and offline; any real id works too."
            else "Inspect model id (provider/model), resolved through inspect's own provider layer. Required: there is deliberately no default real model.";
          suggestions = modelSuggestions;
          order = 1000;
          group = "model";
        } // (if keyless then {
          initial = "mockllm/model";
        } else { }));
        limit = param int {
          description = "Sample cap (0 = the whole dataset).";
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
          fields = genFields;
          order = 1020;
          group = "generation";
        };
      };

    inherit results;
    # catalog tasks download their datasets (usually HuggingFace); hello stays
    # offline. docker only when the eval's declared runtime metadata says sandbox.
    env = lib.optionalAttrs (!keyless) { network = true; }
      // lib.optionalAttrs sandbox { docker = true; };

    # The adapter — the lift between the runner protocol (params JSON on stdin, seed
    # in $REPS_SEED) and the wrapper's interface (`reps-inspect-eval CONFIG.json`):
    # every flattened task param regroups into inspect task_args under its real
    # kwarg name (the catalog's kwarg map un-does the `task_` collision prefix);
    # explicit nulls pass through — the task is called with kwarg=None, exactly
    # its declared default.
    program =
      let
        regroup = "{" + lib.concatStringsSep ", "
          (lib.mapAttrsToList
            (pname: kwarg: ''${builtins.toJSON kwarg}: .[${builtins.toJSON pname}]'')
            paramKwargs) + "}";
      in
      writeShellApplication {
        name = "${name}-adapter";
        runtimeInputs = [ jq ];
        text = ''
          config=$(mktemp)
          trap 'rm -f "$config"' EXIT
          jq --argjson seed "''${REPS_SEED:-0}" '{
            task: ${builtins.toJSON task},
            model: .model,
            limit: .limit,
            epochs: .epochs,
            generate_args: .generate_args,
            seed: $seed,
            task_args: ${regroup}
          }' > "$config"
          ${lib.getExe' env "reps-inspect-eval"} "$config"
        '';
      };
  };
in
lib.mapAttrs mkTask tasks
