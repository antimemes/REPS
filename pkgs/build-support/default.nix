# The reps build-support lib: type constructors and mkExperiment.
# Types are authored here as nix values and rendered to plain JSON descriptors at eval
# time (REPS_MANIFEST); the runner and GUI consume JSON and never evaluate nix.
#
# `rev`/`narHash` describe the source tree being evaluated (null = unknown): the
# flake door passes them from its own sourceInfo; the classic door knows only a
# revision from the .git-revision stamp or repsRev argument. `origin` is the fetchable ref without
# a rev — a source cannot know its own URL, so it is stated once at the call site.
# `reps-runner` arrives by argument, so overriding the runner reaches every
# experiment.
{ pkgs, origin, reps-runner, pyproject-nix, uv2nix, pyproject-build-systems
, rev ? null, narHash ? null
}:

let
  inherit (pkgs) lib;

  # The fetchable reproducibility ref: the pinned rev spliced into the origin ref (in
  # front of any ?dir=... query). This is NOT condition identity (see mkExperiment's
  # per-experiment content `source` below) — it is recorded per run so a run can be
  # re-run from a pinned source.
  refParts = lib.splitString "?" origin;
  fetchRef =
    if rev != null && rev != "" then
      builtins.head refParts + "/${rev}"
      + lib.optionalString (builtins.length refParts > 1)
        "?${lib.concatStringsSep "?" (builtins.tail refParts)}"
    else null;

  # types.llm governs its own hints: every llm-typed param gets the shared model
  # catalog (lib/model-catalog) appended to its suggestions at eval time, so no
  # experiment can desync from it. An experiment's own `suggestions` attr means
  # experiment-specific EXTRAS, prepended (e.g. hello's keyless mock) — never a
  # replacement. Presentation only: hints are not identity.
  llmSuggestions = import ../../lib/model-catalog/suggestions.nix { inherit lib; };
  # Applies to top-level params AND llm-typed fields inside listOf(struct) params
  # (e.g. a roster's per-agent model cell) — a struct field is a bare type or a
  # param-wrapped one; only fields that actually get hints are rewrapped.
  addHints = p: p // { suggestions = (p.suggestions or [ ]) ++ llmSuggestions; };
  withFieldHints = f:
    let p = if f ? kind then { type = f; } else f;
    in if (p.type.kind or null) == "llm" then addHints p else f;
  withLlmHints = params: lib.mapAttrs
    (_: p:
      if (p.type.kind or null) == "llm" then addHints p
      else if (p.type.kind or null) == "list" && (p.type.of.kind or null) == "struct"
      then p // {
        type = p.type // {
          of = p.type.of // { fields = lib.mapAttrs (_: withFieldHints) p.type.of.fields; };
        };
      }
      else p)
    params;

  # Dev-loop artifacts (a `uv run` .venv, __pycache__, build dists, direnv state)
  # are neither identity nor build input: they must not mint new conditions
  # (mkExperiment's `source` hash) and must not be imported into the store at
  # eval time — re-hashing a multi-hundred-MB .venv on every fresh eval was the
  # dominant cost of a classic-door eval.
  isDevArtifact = base:
    builtins.elem base [ ".venv" "__pycache__" "node_modules" "dist" ".direnv" ".pytest_cache" ".mypy_cache" ".ruff_cache" ]
    || base == "result" || lib.hasPrefix "result-" base;
  # import a source path with dev artifacts filtered out; content-addressed, so
  # an unchanged subtree re-imports for free
  cleanImport = name: path: builtins.path {
    inherit name path;
    filter = p: _type: !isDevArtifact (baseNameOf p);
  };
  # uv repeats path dependencies' test requirements in the lock. Exclude only
  # these tables from identity, preserving every other byte and the build input.
  identityLock = name: path:
    let
      filtered = lib.foldl' (state: line:
        let
          table = lib.hasPrefix "[" line;
          skip = if table then line == "[package.metadata.requires-dev]" else state.skip;
        in { inherit skip; lines = if skip then state.lines else [ line ] ++ state.lines; })
        { skip = false; lines = [ ]; } (lib.splitString "\n" (builtins.readFile path));
    in builtins.toFile name (lib.concatStringsSep "\n" (lib.reverseList filtered.lines));
  # Directory declarations hash the unchanged tree separately from normalized
  # locks, retaining each lock's relative path. No build or on-disk rewrite is
  # needed to compute identity; the venv still consumes the original lock.
  identityImport = name: path:
    let
      locksIn = directory: prefix: lib.concatMap (entry:
        let relative = prefix + entry; child = directory + "/${entry}";
        in if isDevArtifact entry then [ ]
           else if (builtins.readDir directory).${entry} == "directory"
           then locksIn child (relative + "/")
           else lib.optional (entry == "uv.lock") relative)
        (builtins.attrNames (builtins.readDir directory));
      isDirectory = builtins.readFileType path == "directory";
      locks = if isDirectory then locksIn path "" else [ ];
    in if !isDirectory && baseNameOf path == "uv.lock" then identityLock name path
       else if locks == [ ] then cleanImport name path
       else builtins.toJSON {
         tree = builtins.path {
           inherit name path;
           filter = p: type: !isDevArtifact (baseNameOf p)
             && !(type != "directory" && baseNameOf p == "uv.lock");
         };
         locks = lib.genAttrs locks (relative: identityLock "uv.lock" (path + "/${relative}"));
       };
  defaultSharedSrcs = [
    ../../lib/reps-events/reps_events
    ../../lib/reps-experiment/reps_experiment
    ../../lib/reps-providers/reps_providers
  ];
in
{
  # exported for the other source-import sites (web dist, the runner workspace) —
  # every path that enters the store goes through the same dev-artifact filter
  inherit cleanImport;
  # Pure evaluation checks exercise these private identity functions directly.
  tests.identity-lock = import ./tests/identity-lock.nix { inherit identityLock identityImport; };
  inherit defaultSharedSrcs;
  inspectSharedSrcs = defaultSharedSrcs ++ [ ../../lib/reps-inspect/reps_inspect ];

  # the generated GenerateConfig sub-form (lib/reps-inspect/generate_fields.json,
  # regenerated by `task genconfig:update`), exposed here so experiment
  # declarations never reach above their own directory with relative paths.
  # The schema comes from whichever reps package set is being evaluated.
  inspectGenerateFields =
    (builtins.fromJSON (builtins.readFile ../../lib/reps-inspect/generate_fields.json)).fields;

  types = rec {
    llm = { kind = "llm"; };
    str = { kind = "str"; };
    int = { kind = "int"; };
    float = { kind = "float"; };
    bool = { kind = "bool"; };
    enum = values: { kind = "enum"; inherit values; };
    listOf = of: { kind = "list"; inherit of; };
    # Only legal as listOf (struct { ... }) — the "table shape". Fields are scalar/registry
    # types only; the runner validates this when loading the manifest.
    struct = fields: { kind = "struct"; inherit fields; };
    # free-form JSON object — arbitrary keys/values, for a wrapped tool's passthrough
    # args (inspect's -T/generate config). The GUI renders it as a JSON editor; the
    # whole object is materialized into the condition hash as written.
    object = { kind = "object"; };

    # param wraps a type with defaults/instantiation-checks/presentation hints.
    param = type: attrs: { inherit type; } // attrs;
  };

  # mkPythonEnv: shared uv2nix setup using the toolchain supplied by the entrypoint.
  # Python sources come from uv.lock; local sources are filtered without
  # changing which directory or revision the lock selects.
  mkPythonEnv = lib.makeOverridable
    ({ name
    , workspaceRoot
    , python
    , sourcePreference ? "wheel"
      # Upstreams without packaging cannot be uv dependencies. Define each
      # package in the experiment's overrides overlay and install it here.
    , extraPackages ? [ ]
    , groups ? [ ]
      # extra pythonSet overlay for per-experiment-directory fixes (e.g. a git dep that ships a
      # legacy setup.py and needs setuptools injected as a build system). Receives
      # (final: prev: …) with uv2nix's pythonSet, where `final.resolveBuildSystem`
      # is available.
    , overrides ? (_final: _prev: { })
    }:
    let
      projectName = (builtins.fromTOML (builtins.readFile (workspaceRoot + "/pyproject.toml"))).project.name;
      workspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };
      # Let uv2nix resolve sources, then filter local path sources using the
      # documented per-package overrideAttrs interface. Fetchers remain intact.
      projectOverlay = workspace.mkPyprojectOverlay { inherit sourcePreference; };
      filteredProjectOverlay = final: prev:
        lib.mapAttrs
          (_: package: package.overrideAttrs (old:
            lib.optionalAttrs (builtins.isPath (old.src or null)) {
              src = cleanImport "${old.pname}-src" old.src;
            }))
          (projectOverlay final prev);
      pythonSet =
        (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
          (lib.composeManyExtensions [
            pyproject-build-systems.overlays.default
            filteredProjectOverlay
            overrides
          ]);
    in
    # Select the application only; editable local test dependencies are also
    # workspace members, but must not become runtime installation roots.
    assert lib.assertMsg (builtins.all
      (group: builtins.elem group (workspace.deps.groups.${projectName} or [ ])) groups)
      "mkPythonEnv: unknown dependency group for ${projectName}";
    pythonSet.mkVirtualEnv name ({ ${projectName} = groups; } // lib.genAttrs extraPackages (_: [ ])));

  # Like nixpkgs testers.*, these shared builders turn test declarations into
  # ordinary derivations. Experiments supply data and hooks in tests/default.nix;
  # overrideAttrs is the escape hatch, never test machinery in package.nix.
  testers = {
    pytest = { experiment, tests, env ? { }, preCheck ? "", flags ? [ ] }:
      pkgs.stdenv.mkDerivation {
        name = "${experiment.name}-pytest";
        src = cleanImport "${experiment.name}-tests" tests;
        nativeBuildInputs = [ (experiment.program.override { groups = [ "dev" ]; }) ];
        inherit env;
        dontBuild = true;
        doCheck = true;
        preCheck = ''export HOME="$TMPDIR"'' + "\n" + preCheck;
        # Bound concurrent numeric imports in the sandbox. Explicit flags can
        # still select serial execution for debugging.
        checkPhase = "runHook preCheck; python -m pytest ${lib.escapeShellArgs ([ "-n" "8" ] ++ flags)}; runHook postCheck";
        installPhase = ''touch "$out"'';
      };
    # params is the complete condition; the smoke run never uses manifest initials.
    smoke = { experiment, params, env ? { } }:
      pkgs.testers.runCommand {
        name = "${experiment.name}-smoke";
        nativeBuildInputs = [ experiment.app reps-runner ];
        inherit env;
        inherit (experiment) manifest;
        launcher = lib.getExe experiment.app;
        sets = lib.escapeShellArgs (lib.concatMap
          (n: [ "--set" "${n}=${builtins.toJSON params.${n}}" ])
          (builtins.attrNames params));
        script = builtins.readFile ./smoke.sh;
      };
  };

  # mkExperiment: the schema + program → a runnable flake app with the manifest JSON and
  # source identity baked in. `program` is whatever speaks the runner protocol (params
  # JSON on stdin → events through REPS_EVENT_SOCKET): a derivation (resolved via lib.getExe — the
  # usual case, a writeShellApplication adapter next to package.nix) or an explicit
  # executable path string.
  mkExperiment =
    { name
    , summary
    , params
    , results ? [ ]
    , schema ? { version = 0; models = "reps_events:Payload"; }
    , schemaPython ? "${reps-runner}/bin/python"
    , sharedSrcs ? defaultSharedSrcs
    , env ? { }
      # external references for the experiment page — [{ label, url }]: the upstream
      # repo/docs, paper, datasets. Presentation only, never identity (not in `src`):
      # generated from a wrapped package's own declared metadata where it exists
      # (inspect_evals' listing), hand-written in the wrapper otherwise.
    , links ? [ ]
      # Plain Markdown catalog text, supplied from the package directory by the
      # registry. This field does not alter the experiment's declared `src`.
    , readme ? null
    , readmeAssets ? null
    , program
    , src           # the experiment's OWN identity sources: a path (usually `./.`) or a
                    # list of paths. See identity note below. Identity is strictly
                    # content — it records, never judges comparability; declared
                    # upstream versions are covariates/advisory input, not identity
                    # (docs/book/src/running/model.md).
    }:
    assert lib.assertMsg (builtins.isList results && builtins.all
      (result: builtins.isAttrs result && builtins.isString (result.name or null) && result ? type)
      results) "mkExperiment.results must be a list of declarations with name and type";
    assert lib.assertMsg (builtins.length (lib.unique (map (result: result.name) results))
      == builtins.length results) "mkExperiment.results has duplicate result names";
    assert builtins.isInt schema.version && schema.version >= 0;
    assert builtins.isString schema.models
      && builtins.match "[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*" schema.models != null;
    let
      programPath =
        if lib.isDerivation program then lib.getExe program else program;

      # Per-experiment content identity. The condition hash's `source` is a content
      # hash of this experiment's declared sources and shared Python package sources,
      # each re-imported via builtins.path
      # so its hash depends ONLY on that subtree's content, never on the whole-repo
      # rev. So editing one experiment's directory cannot change another's condition_id.
      # Interpreter declarations are source; platform and runner closures,
      # web, docs and repeated lock dev metadata are outside identity.
      # The fetchable rev (`fetchRef`) is recorded
      # separately for reproducibility — also not identity.
      srcs = (if builtins.isList src then src else [ src ]) ++ sharedSrcs;
      # dev-loop artifacts are NOT identity (isDevArtifact above): a `uv run`
      # dropping a .venv into the subtree must not mint new conditions
      source = "content:sha256:" + builtins.hashString "sha256" (lib.concatStringsSep "\n"
        (lib.imap0 (i: p: "${identityImport "reps-src-${name}-${toString i}" p}") srcs));

      # `origin` identifies the packaging repository in catalog metadata; it is
      # not part of `src` and is distinct from the pinned fetchRef
      manifestInput = pkgs.writeText "reps-manifest-${name}-input.json" (builtins.toJSON ({
        schema_version = 1;
        params = withLlmHints params;
        # Result order is authored explicitly and preserved on the wire.
        inherit name summary env origin links schema results;
      } // lib.optionalAttrs (readme != null) { inherit readme; }));
      # Import from the built program environment. A bad module:attr pointer is
      # a build error, before the launcher or catalog can be used.
      schemaInterpreter = pkgs.writeShellScript "reps-schema-python-${name}" ''
        exec ${schemaPython} "$@"
      '';
      schemaBundle = pkgs.runCommand "reps-schema-${name}" { } ''
        mkdir -p "$out"
        # Verification imports the exact declared union in its built environment.
        ln -s ${schemaInterpreter} "$out/python"
        ${schemaPython} -m reps_events.export ${lib.escapeShellArg schema.models} > "$out/schema.json"
        ${schemaPython} - <<'PY'
        from importlib.resources import files
        import json, os
        from pathlib import Path
        from reps_events import VOCABULARY_VERSION
        out = Path(os.environ["out"])
        (out / "shared-schema.json").write_text(files("reps_events").joinpath("schema.json").read_text())
        manifest = json.loads(Path("${manifestInput}").read_text())
        manifest["v"] = VOCABULARY_VERSION
        manifest["schema"]["path"] = str(out / "schema.json")
        (out / "manifest.json").write_text(json.dumps(manifest))
        PY
      '';
      manifest = "${schemaBundle}/manifest.json";
      app = assert lib.assertMsg
        (fetchRef == null || builtins.match "([^:]+://)?[^/?#]*@(.|[[:space:]])*" fetchRef == null)
        "fetch_ref must not contain userinfo";
        pkgs.writeShellApplication {
        name = "reps-${name}";
        runtimeInputs = [ reps-runner ];
        text = ''
          export REPS_MANIFEST=${manifest}
          export REPS_EXPERIMENT_BIN=${lib.escapeShellArg programPath}
          export REPS_RUNNER_BIN=${lib.escapeShellArg (lib.getExe reps-runner)}
          export REPS_NIX_SYSTEM=${lib.escapeShellArg pkgs.stdenv.hostPlatform.system}
          export REPS_SOURCE=${lib.escapeShellArg source}
          export REPS_FETCH_REF=${lib.escapeShellArg (if fetchRef != null then fetchRef else "")}
          export REPS_TREE_HASH=${lib.escapeShellArg (if narHash != null then narHash else "")}
          exec "$REPS_RUNNER_BIN" "$@"
        '';
      };
    in
    { inherit app manifest name readmeAssets source; }
    // lib.optionalAttrs (lib.isDerivation program) { inherit program; };
}
