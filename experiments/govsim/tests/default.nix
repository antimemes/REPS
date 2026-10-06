{ experiment, reps }: {
  pytest = reps.testers.pytest {
    inherit experiment;
    tests = ./.;
    env = { HF_HUB_OFFLINE = "1"; TRANSFORMERS_OFFLINE = "1"; };
    preCheck = "unset SSL_CERT_FILE"; # httpx initializes TLS even in offline tests
  };
  smoke = reps.testers.smoke {
    inherit experiment;
    params = {
      experiment = "fish_baseline_concurrent";
      model = "mock/model";
      embedder = "hash";
      max_rounds = 1;
      max_tokens = 8000;
      threads = 2;
      reasoning_effort = null;
      temperature = 0.0;
      top_p = 1.0;
    };
  };
}
