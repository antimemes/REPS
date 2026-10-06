# Concordia (google-deepmind's generative agent-based modeling library) as a REPS
# experiment: multi-agent social simulations, translated live into REPS events.
#
# ONE experiment: Concordia scenarios are data interpreted by one program, so the
# scenario — the cast of `agents`, the `premise`, the `game_master` — is composed in
# params (the webui renders the roster as an editable table), not baked into cards.
# Cards mark code boundaries (inspect tasks are different programs); params mark data.
#
# Every configuration runs keyless on `mock/model` (deterministic scripted lines, no
# network), against any OpenAI-compatible /chat/completions server via
# `openai/<served-name>`, or against a hosted provider's OpenAI-compatibility
# endpoint (`anthropic/`, `google/`, `groq/`, `mistral/`, `grok/`, `openrouter/`,
# `azureai/` — see reps_experiment/providers.py) — endpoint and key come from the
# credential store, never from params.
{ reps, pkgs, lib, writeShellApplication, jq }:

let
  env = reps.mkPythonEnv {
    name = "reps-concordia-env";
    workspaceRoot = ./.;
    python = pkgs.python313;
  };

  mockSuggestion = {
    value = "mock/model";
    description = "Keyless offline mock — deterministic scripted lines, no network (the smoke/CI path).";
  };

  # the initial scenario: two old friends at a cafe — keyless, so the prefilled
  # command works with zero setup. Presentation only, like every initial.
  cafeAgents = [
    { name = "Alice"; goal = "Catch up warmly and find out how Bob has been."; model = ""; }
    { name = "Bob"; goal = "Share what has changed in your life since you last met."; model = ""; }
  ];
in
{
  concordia = reps.mkExperiment {
    schemaPython = "${env}/bin/python";
    name = "concordia";
    # NOT ./. — tests are not behavior, so a test edit re-versions nothing
    src = [ ./package.nix ./pyproject.toml ./uv.lock ./concordia_sim ];
    summary = "A Concordia generative agent simulation: compose the cast, their goals, and the premise; the transcript streams back as events.";
    # hand-written (no upstream link registry): the wrapped library and its paper
    links = [
      { label = "paper"; url = "https://arxiv.org/abs/2312.03664"; }
      { label = "source"; url = "https://github.com/google-deepmind/concordia"; }
    ];
    params = with reps.types; {
      agents = param (listOf (struct {
        name = param str { description = "Character name (the message `from` in the transcript)."; };
        goal = param str { description = "What this character is trying to do — shapes how they act each turn."; };
        model = param llm {
          description = "Per-agent model override; empty = use the run's `model`. Mixing models across the cast is a treatment axis.";
          suggestions = [ mockSuggestion ];
        };
      })) {
        description = "The cast of the simulation — one row per character. This, with the premise, is the scenario.";
        initial = cafeAgents;
        minLen = 2;
        order = 1;
        group = "scenario";
      };
      premise = param str {
        description = "The opening situation the game master narrates to set the scene.";
        initial = "Alice and Bob, old friends who have not spoken in months, run into each other at a small cafe on a rainy afternoon.";
        order = 2;
        group = "scenario";
      };
      game_master = param str {
        description = "Concordia game-master prefab driving the scene (a module under concordia.prefabs.game_master). `dialogic` = pure conversation; `generic` narrates events.";
        initial = "dialogic";
        suggestions = [
          { value = "dialogic"; description = "Pure back-and-forth conversation (recommended for dialogue scenes)."; }
          { value = "generic"; description = "Narrates world events between turns."; }
          { value = "situated"; description = "Grounds agents in a physical setting."; }
          { value = "game_theoretic_and_dramaturgic"; description = "Payoff-matrix framing over the scene."; }
        ];
        order = 3;
        group = "scenario";
      };
      max_steps = param int {
        description = "Step budget — one agent turn each. An upper bound: a game master that can end the scene (e.g. `generic`) may finish sooner; `dialogic` always runs the full budget.";
        initial = 6;
        order = 4;
        group = "scenario";
      };
      model = param llm {
        description = "Model for the game master and every agent whose roster row leaves its override empty (provider/model). `mock/model` runs keyless and offline; `openai/<served-name>` reaches any OpenAI-compatible /chat/completions server; `anthropic/`, `google/`, `groq/`, `mistral/`, `grok/`, `openrouter/`, and `azureai/` reach those providers' OpenAI-compatibility endpoints.";
        initial = "mock/model";
        suggestions = [ mockSuggestion ];
        order = 1000;
        group = "model";
      };
      temperature = param float {
        description = "Sampling temperature for the real backend (the mock ignores it).";
        initial = 0.5;
        order = 1020;
        group = "generation";
      };
      max_tokens = param int {
        description = "Per-call completion budget for the real backend (the mock ignores it).";
        initial = 256;
        order = 1021;
        group = "generation";
      };
    };
    results = with reps.types; [
      { name = "steps"; type = int; }         # simulation steps actually run
      { name = "agents"; type = int; }        # size of the scenario roster
      { name = "world_events"; type = int; }  # world-channel messages (premise + agent turns)
      { name = "model_calls"; type = int; }   # llm.call events emitted
    ];
    env = { network = true; };
    # the lift between the runner protocol (flat params on stdin, seed in $REPS_SEED)
    # and the sim's config file: rename model -> default_model, merge the seed.
    program = writeShellApplication {
      name = "concordia-adapter";
      runtimeInputs = [ jq ];
      text = ''
        config=$(mktemp)
        trap 'rm -f "$config"' EXIT
        jq --argjson seed "''${REPS_SEED:-0}" '{
          agents: .agents,
          premise: .premise,
          game_master: .game_master,
          default_model: .model,
          max_steps: .max_steps,
          temperature: .temperature,
          max_tokens: .max_tokens,
          seed: $seed
        }' > "$config"
        ${lib.getExe' env "concordia-sim"} "$config"
      '';
    };
  };
}
