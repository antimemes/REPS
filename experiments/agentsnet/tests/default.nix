{ experiment, reps }: {
  pytest = reps.testers.pytest {
    inherit experiment;
    tests = ./.;
    # the authors' tree and the dataset file the launcher points at (package.nix hangs them on the program)
    env = {
      AGENTSNET_UPSTREAM = "${experiment.program.upstream}";
      AGENTSNET_UPSTREAM_REV = experiment.program.upstreamRev;
      AGENTSNET_DATASET = "${experiment.program.dataset}";
      HF_HUB_OFFLINE = "1";
    };
    preCheck = "unset SSL_CERT_FILE"; # httpx initializes TLS even in offline tests
  };
  smoke = reps.testers.smoke {
    inherit experiment;
    # coloring: its scorer divides by the edge count, so no answer pattern can
    # raise under the mock's seed-dependent picks (vertex cover's divides by the
    # cover size, which an all-"No" pick makes zero: upstream's own crash, which
    # the adapter records as score 0 with upstream_crashed)
    params = {
      model = "mock/model"; task = "coloring"; graph_generator = "ws"; graph_size = 4; graph_index = 0;
      rounds = 2; chain_of_thought = true;
    };
    env.HF_HUB_OFFLINE = "1";
  };
}
