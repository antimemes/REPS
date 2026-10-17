{ experiment, reps, lib, runCommand, gnupatch }:
let
  patches = builtins.attrNames (builtins.readDir experiment.program.patches);
  # each subset of the fixes, applied in name sequence as the adapter does
  subsets = lib.foldl' (acc: name: acc ++ map (s: s ++ [ name ]) acc) [ [ ] ] patches;
in
{
  # each combination of switches must apply cleanly, with no fuzz, to the pinned tree
  patches = runCommand "agentsnet-patches-apply" { nativeBuildInputs = [ gnupatch ]; } (lib.concatMapStrings (subset: ''
    rm -rf tree && cp -r ${experiment.program.upstream} tree && chmod -R u+w tree
    ${lib.concatMapStrings (name: ''
      patch -d tree -p1 --fuzz=0 --batch --forward -i ${experiment.program.patches}/${name}
    '') (lib.sort (a: b: a < b) subset)}
  '') subsets + "touch $out\n");
  pytest = reps.testers.pytest {
    inherit experiment;
    tests = ./.;
    # the authors' tree and the dataset file the launcher points at (package.nix hangs them on the program)
    env = {
      AGENTSNET_UPSTREAM = "${experiment.program.upstream}";
      AGENTSNET_UPSTREAM_REV = experiment.program.upstreamRev;
      AGENTSNET_DATASET = "${experiment.program.dataset}";
      AGENTSNET_PATCHES = "${experiment.program.patches}";
      HF_HUB_OFFLINE = "1";
    };
    # httpx initializes TLS even in offline tests; staging applies the fixes with patch
    preCheck = "unset SSL_CERT_FILE; export PATH=${gnupatch}/bin:$PATH";
  };
  smoke = reps.testers.smoke {
    inherit experiment;
    # coloring: its scorer divides by the edge count, thus no answer pattern can
    # raise with the mock's seed-dependent picks (vertex cover's divides by the
    # cover size, which an all-"No" pick makes zero: upstream's crash, which
    # the adapter records as score 0 with upstream_crashed)
    params = {
      model = "mock/model"; task = "coloring"; graph_generator = "ws"; graph_size = 4; graph_index = 0;
      rounds = 2; chain_of_thought = true; patch_crash_on_all_no = true; better_reprompting = true;
    };
    env.HF_HUB_OFFLINE = "1";
  };
}
