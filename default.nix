# Classic entrypoint — plain Nix, nixpkgs-style: no flakes, no flake-compat. The
# flake and this file are two doors into the same pkgs/top-level; nixpkgs is pinned
# to the SAME revision by reading the authoritative flake.lock.
#
#   nix-build -A experiment-inspect-hello && ./result/bin/reps-inspect-hello …
#   nix-build https://github.com/antimemetics-institute/reps/archive/main.tar.gz \
#     -A experiment-inspect-hello
#
# Provenance: git stamps the commit hash into .git-revision when generating an
# archive (export-subst — GitHub tarballs included), so tarball builds know their
# rev and runs record a real fetchable ref. In a checkout the placeholder stays
# unexpanded and runs omit fetch_ref — a working tree has no pinned clean rev. The
# `repsRev` argument overrides the stamp (the fetchGit flow states its own rev).
let
  lockedSources = import ./pkgs/locked-sources.nix { };
  stampRev =
    let m = builtins.match "([0-9a-f]{40})[[:space:]]*" (builtins.readFile ./.git-revision);
    in if m == null then null else builtins.head m;
in
{ system ? builtins.currentSystem
, nixpkgs ? lockedSources.nixpkgs
, uv2nix ? lockedSources.uv2nix
, pyproject-nix ? lockedSources.pyproject-nix
, pyproject-build-systems ? lockedSources.pyproject-build-systems
, pkgs ? import nixpkgs { inherit system; }
, repsRev ? null
}:
let
  inherit (pkgs) lib;
  repsPkgs = import ./pkgs/top-level {
    inherit pkgs uv2nix pyproject-nix pyproject-build-systems;
    rev = if repsRev != null then repsRev else stampRev;
  };

  runnables =
    { inherit (repsPkgs) reps-runner; }
    // lib.mapAttrs' (name: exp: lib.nameValuePair "experiment-${name}" exp.app)
      repsPkgs.experiments;
in
runnables
// {
  inherit pkgs;
  manifests = repsPkgs.manifests;


  # `exec.<name>`: the classic mirror of the flake's APP namespace — bare names for
  # experiments, reps- prefix for tools, exactly like `nix run .#<name>`. The output
  # IS the executable (a symlink resolved via lib.getExe, i.e. meta.mainProgram), so
  # the flakeless one-liner needs no binary-name knowledge:
  #   $(nix-build --no-out-link -A exec.inspect-hello) --set …
  exec = lib.mapAttrs
    (name: drv: pkgs.runCommand "exec-${name}" { } "ln -s ${lib.getExe drv} $out")
    ({ inherit (repsPkgs) reps-runner; }
      // lib.mapAttrs (_: exp: exp.app) repsPkgs.experiments);
}
