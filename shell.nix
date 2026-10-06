# The dev shell — imported by the flake (devShells.default), also usable standalone
# via legacy `nix-shell` (default.nix supplies the SAME flake.lock-pinned nixpkgs).
# Toolchains only: per-project Python deps come from each project's pyproject/uv.lock
# via `uv run`; the shell never duplicates dependency lists.
{ pkgs ? (import ./. { }).pkgs }:

pkgs.mkShell {
  packages = [
    # python projects (runner, lib/reps-inspect, experiment families) — uv drives them;
    # python is here solely as uv's interpreter (only-system preference: uv-managed
    # standalone builds are FHS-linked and break on NixOS)
    pkgs.uv
    pkgs.python313

    # dev drivers & utilities
    pkgs.go-task
    pkgs.jq

    # python typechecker (task typecheck): pyright at STRICT (per-package
    # pyrightconfig.json) — chosen over ty for its annotation-completeness rules
    # (no implicit Unknown, no bare generics); from nixpkgs, NOT a per-project dev dep —
    # per-project npm copies would drift, and one pinned copy
    # beats five. CI runs task typecheck through this same shell (ci.yml, nix job),
    # so the flake pin is the single source of the version.
    pkgs.pyright

    pkgs.mdbook

    # sandboxed experiments (inspect's docker sandbox) need a Docker daemon; the
    # devshell ships the whole engine (client + compose plugin + dockerd) and
    # `task docker:up` sudo-starts the daemon on the default socket.
    # fuse-overlayfs: dockerd's wrapper doesn't bundle it, and it's the only
    # storage driver that works when data-root sits on virtiofs (kernel overlayfs
    # refuses a virtiofs upperdir).
    pkgs.docker
    pkgs.fuse-overlayfs
  ];

  env = {
    # never let uv download a standalone interpreter — use the nix one
    UV_PYTHON_PREFERENCE = "only-system";
  };

  # manylinux wheels (numpy/pandas under inspect_ai, …) dlopen the C++
  # runtime and zlib; nix's bare interpreter has neither on the loader path, so plain
  # `uv run` in a uv2nix-less venv fails with `libstdc++.so.6: cannot open`. Put them
  # on LD_LIBRARY_PATH so `uv run pytest` etc. work without a manual export.
  shellHook = ''
    export LD_LIBRARY_PATH=${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
  '';
}
