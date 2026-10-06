# The wiring: experiments/ is autoimported into a callPackage scope; REPS's own tools
# are defined here, all-packages.nix style. Entries are functions over their
# dependencies; the scope injects them by argument name and makes everything
# overridable.
#
# The registry convention:
#
#   experiments/<dir>/package.nix → an attrset of experiments, one attr per
#   experiment name. One directory usually declares one experiment, but may declare
#   several backed by the same code (impossiblebench, inspect_evals). Each value is
#   an reps.mkExperiment result ({ app, manifest, name }).
#
# experiments/ holds experiment code only; everything REPS-specific (this wiring,
# build-support, tool packaging) lives under pkgs/ and the tools' own source trees
# (runner/).
{ pkgs, pyproject-nix, uv2nix, pyproject-build-systems, rev ? null, narHash ? null }:

let
  inherit (pkgs) lib;

  origin = "github:antimemetics-institute/reps";

  experimentsDir = ../../experiments;

  dirNames =
    if builtins.pathExists experimentsDir then
      lib.attrNames
        (lib.filterAttrs
          (name: _: builtins.pathExists (experimentsDir + "/${name}/package.nix"))
          (builtins.readDir experimentsDir))
    else [ ];

  scope = lib.makeScope pkgs.newScope (final:
    {
      pyproject-nix = import pyproject-nix { inherit lib; };
      uv2nix = import uv2nix { inherit lib; inherit (final) pyproject-nix; };
      pyproject-build-systems = import pyproject-build-systems {
        inherit lib;
        inherit (final) pyproject-nix uv2nix;
      };

      # build support — the `reps` attrset experiment declarations take as an argument.
      reps = final.callPackage ../build-support {
        inherit origin rev narHash;
      };

      # REPS's own tools; the reps- prefix keeps them out of the registry's bare namespace.
      # The runner packages itself from its own uv.lock (uv2nix) — see runner/default.nix,
      # including why its workspace import cannot go through reps.cleanImport.
      reps-runner = final.callPackage ../../runner { };

    }
    # experiments/<dir>/package.nix → { <experiment-name> = mkExperiment …; }
    // lib.mapAttrs'
      (name: _:
        let
          directory = experimentsDir + "/${name}";
          markdown = directory + "/README.md";
          hasMarkdown = builtins.pathExists markdown;
        in
        if builtins.pathExists (directory + "/README.mdx") then
          throw "experiment ${name}: README.mdx is no longer supported; write README.md"
        else lib.nameValuePair "experiments-${name}"
        (final.callPackage (directory + "/package.nix") {
          # A directory can declare several experiments; they share its README.
          # Markdown ships in manifests, with images in the catalog assets.
          reps = final.reps // {
            mkExperiment = args: let experiment = final.reps.mkExperiment (args // {
              readme = if hasMarkdown then builtins.readFile markdown else null;
              # Package committed images; illustration tools are never build inputs.
              readmeAssets = lib.cleanSourceWith {
                src = directory;
                name = "reps-readme-assets-${name}";
                filter = path: type: (type == "directory"
                  && !(lib.hasPrefix "." (baseNameOf path))
                  && !(builtins.elem (baseNameOf path) [ "node_modules" "__pycache__" ])) ||
                  (type == "regular" && builtins.match ".*\\.(svg|png|jpg|jpeg|gif|webp)" path != null);
              };
            }); in experiment // lib.optionalAttrs (builtins.pathExists (directory + "/tests/default.nix")) {
              # Keep nixpkgs' passthru.tests convention on our experiment attrset.
              passthru = (experiment.passthru or { }) // {
                tests = removeAttrs
                  (final.callPackage (directory + "/tests/default.nix") { inherit experiment; })
                  [ "override" "overrideDerivation" ];
              };
            };
          };
        }))
      (lib.genAttrs dirNames (_: null)));

  # flatten the per-directory sets into the experiment registry, refusing name
  # collisions (callPackage decorates each set with override/overrideDerivation —
  # drop those)
  registry = lib.foldl'
    (acc: setRaw:
      let
        set = removeAttrs setRaw [ "override" "overrideDerivation" ];
        dup = builtins.attrNames (builtins.intersectAttrs acc set);
      in
      if dup != [ ] then throw "duplicate experiment name(s): ${toString dup}"
      else acc // set)
    { }
    (map (name: scope."experiments-${name}") dirNames);
  catalog = pkgs.linkFarm "reps-manifests"
    (lib.concatLists (lib.mapAttrsToList
      (name: exp: [{ name = "${name}.json"; path = exp.manifest; }]
        ++ lib.optional (exp.readmeAssets != null) {
          name = "assets/${name}"; path = exp.readmeAssets;
        }) registry));

in
{
  experiments = registry;
  manifests = catalog;
  inherit (scope) reps reps-runner;
}
