# The shipped CLI includes publishing; fixture consumers can use the base library.
# reps-runner, packaged from its own uv.lock with the shared locked Python toolchain.
# The interpreter is selected here, per project.
{ pkgs, pyproject-nix, uv2nix, pyproject-build-systems }:

let
  inherit (pkgs) lib;

  # 3.13, not 3.14: binary-wheel coverage. One-line bump when cp314 wheels are universal.
  python = pkgs.python313;

  # ./. stays a LIVE path, not a filtered store import (cf. reps.cleanImport): the
  # lock path-deps on ../lib/*, which loadWorkspace must resolve at eval — from a
  # store-imported root, `../lib` normalizes to the malformed store path
  # /nix/store/lib and eval dies. Costs importing the runner's own .venv (small).
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

  pythonSet =
    (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
      (lib.composeManyExtensions [
        pyproject-build-systems.overlays.default
        (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
      ]);
in
# mainProgram: the venv carries several bins (reps-runner, reps-emit, python…) —
# name the canonical one so lib.getExe (and anything mainProgram-aware) resolves
# to reps-runner instead of guessing from the derivation name
(pythonSet.mkVirtualEnv "reps-runner-env" { reps-runner = [ "publish" ]; }).overrideAttrs
  (old: { meta = (old.meta or { }) // { mainProgram = "reps-runner"; }; })
