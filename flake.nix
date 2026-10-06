{
  description = "REPS: replications of multi-agent safety experiments";

  # channel tarball, not github: channels.nixos.org serves an immutable-link header, so
  # the lock pins the permanent release URL (and it's what nix-channel users mirror).
  # Stable, not unstable: experiment closures and env fingerprints should churn on
  # NixOS releases (~6 months), not on every channel advance.
  inputs.nixpkgs.url = "https://channels.nixos.org/nixos-26.05/nixexprs.tar.xz";

  inputs.pyproject-nix = { url = "github:pyproject-nix/pyproject.nix"; flake = false; };
  inputs.uv2nix = { url = "github:pyproject-nix/uv2nix"; flake = false; };
  inputs.pyproject-build-systems = { url = "github:pyproject-nix/build-system-pkgs"; flake = false; };

  outputs = { self, nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system:
        f (import nixpkgs { inherit system; }));
    in
    {
      # Namespace policy (nixpkgs-flat, like `nixpkgs#dig`): experiments get bare names —
      # they are the product and the headline oneliner (`nix run reps#inspect-hello`).
      # REPS's own tools carry the `reps-` prefix (`reps-runner`), so the bare
      # namespace belongs to the registry and name collisions are a curation duty, as
      # in nixpkgs.
      apps = forAllSystems (pkgs:
        let
          repsPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
        in
        builtins.mapAttrs
          (name: exp: {
            type = "app";
            program = "${exp.app}/bin/reps-${name}";
          })
          repsPkgs.experiments
        # `nix run .#reps-runner -- credentials …` to manage local model credentials
        # (endpoints + keys), which the runner injects into experiments so they never
        # land in params or on the command line.
        // {
          reps-runner = {
            type = "app";
            program = "${repsPkgs.reps-runner}/bin/reps-runner";
          };
        });


      packages = forAllSystems (pkgs:
        let
          repsPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
        in
        {
          inherit (repsPkgs) reps-runner;
          manifests = repsPkgs.manifests;
        }
        // nixpkgs.lib.mapAttrs' (name: exp: nixpkgs.lib.nameValuePair "experiment-${name}" exp.app)
          repsPkgs.experiments);

      # lean by design: pure builds only (registry-wide manifest eval + the tools).
      # The impure entrypoint identity checks
      # can't be derivations (they invoke nix itself / need network) and live in
      # scripts/ + CI instead.
      checks = forAllSystems (pkgs:
        let
          repsPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
          experimentChecks = nixpkgs.lib.mapAttrs
            (name: experiment: pkgs.linkFarm "${name}-tests"
              (nixpkgs.lib.mapAttrsToList (test: path: { name = test; inherit path; })
                experiment.passthru.tests))
            (nixpkgs.lib.filterAttrs (_: experiment: (experiment.passthru.tests or { }) != { })
              repsPkgs.experiments);
        in
        {
          inherit (repsPkgs) manifests reps-runner;
          identity-lock = pkgs.runCommand "identity-lock" { } ''
            echo ${nixpkgs.lib.escapeShellArg (builtins.toJSON repsPkgs.reps.tests.identity-lock)} > $out
          '';
        } // experimentChecks);

      devShells = forAllSystems (pkgs: {
        default = import ./shell.nix { inherit pkgs; };
      });
    };
}
