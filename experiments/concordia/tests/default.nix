{ experiment, reps, pkgs }: {
  pytest = (reps.testers.pytest {
    inherit experiment;
    tests = ./.;
    env = { HF_HUB_OFFLINE = "1"; TRANSFORMERS_OFFLINE = "1"; };
    preCheck = "unset SSL_CERT_FILE"; # httpx initializes TLS even in offline tests
  }).overrideAttrs {
    # The program is a shell adapter, not a Python venv. Select its test venv
    # explicitly without changing the identity-bearing package declaration.
    nativeBuildInputs = [ (reps.mkPythonEnv {
      name = "concordia-test-env";
      workspaceRoot = ../.;
      python = pkgs.python313;
      groups = [ "dev" ];
    }) ];
  };
  smoke = reps.testers.smoke {
    inherit experiment;
    params = {
      model = "mock/model";
      max_steps = 1;
      agents = [
        { name = "Alice"; goal = "Catch up warmly and find out how Bob has been."; model = ""; }
        { name = "Bob"; goal = "Share what has changed in your life since you last met."; model = ""; }
      ];
      premise = "Alice and Bob, old friends who have not spoken in months, run into each other at a small cafe on a rainy afternoon.";
      game_master = "dialogic";
      temperature = 0.5;
      max_tokens = 256;
    };
  };
}
