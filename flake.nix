{
  description = "ADB — Agent Databank: reusable agent experiments + a databank of runs";

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
      # they are the product and the headline oneliner (`nix run adb#inspect-hello`).
      # ADB's own tools carry the `adb-` prefix (`adb-runner`), so the bare
      # namespace belongs to the registry and name collisions are a curation duty, as
      # in nixpkgs.
      apps = forAllSystems (pkgs:
        let
          adbPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
        in
        builtins.mapAttrs
          (name: exp: {
            type = "app";
            program = "${exp.app}/bin/adb-${name}";
          })
          adbPkgs.experiments
        # `nix run .#adb-runner -- credentials …` to manage local model credentials
        # (endpoints + keys), which the runner injects into experiments so they never
        # land in params or on the command line.
        // {
          adb-runner = {
            type = "app";
            program = "${adbPkgs.adb-runner}/bin/adb-runner";
          };
        });


      packages = forAllSystems (pkgs:
        let
          adbPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
        in
        {
          inherit (adbPkgs) adb-runner;
          manifests = adbPkgs.manifests;
        }
        // nixpkgs.lib.mapAttrs' (name: exp: nixpkgs.lib.nameValuePair "experiment-${name}" exp.app)
          adbPkgs.experiments);

      # lean by design: pure builds only (registry-wide manifest eval + the tools).
      # The impure entrypoint identity checks
      # can't be derivations (they invoke nix itself / need network) and live in
      # scripts/ + CI instead.
      checks = forAllSystems (pkgs:
        let
          adbPkgs = import ./pkgs/top-level { inherit pkgs pyproject-nix uv2nix pyproject-build-systems; rev = self.rev or null; narHash = self.narHash or null; };
          experimentChecks = nixpkgs.lib.mapAttrs
            (name: experiment: pkgs.linkFarm "${name}-tests"
              (nixpkgs.lib.mapAttrsToList (test: path: { name = test; inherit path; })
                experiment.passthru.tests))
            (nixpkgs.lib.filterAttrs (_: experiment: (experiment.passthru.tests or { }) != { })
              adbPkgs.experiments);
        in
        {
          inherit (adbPkgs) manifests adb-runner;
          identity-lock = pkgs.runCommand "identity-lock" { } ''
            echo ${nixpkgs.lib.escapeShellArg (builtins.toJSON adbPkgs.adb.tests.identity-lock)} > $out
          '';
        } // experimentChecks);

      devShells = forAllSystems (pkgs: {
        default = import ./shell.nix { inherit pkgs; };
      });
    };
}
