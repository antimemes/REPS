# The bundled hello task is this family's offline integration check. Test wiring
# is outside the task family's identity sources; no other task is launched here.
{ lib, fetchurl, experiment, reps }:
let
  # Inspect's mock counts tokens with tiktoken. Supply its pinned vocabulary as
  # a build input so the built launcher itself runs without network access.
  encodingUrl = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken";
  encoding = fetchurl {
    url = encodingUrl;
    sha256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d";
  };
in
lib.optionalAttrs (experiment.name == "inspect-hello") {
  smoke = (reps.testers.smoke {
    inherit experiment;
    # Inspect reports "mockllm" as the served name irrespective of the mock alias.
    params = { model = "mockllm/mockllm"; limit = 0; epochs = 1; generate_args = { }; };
  }).overrideAttrs {
    preCheck = ''
      mkdir -p "$TMPDIR/data-gym-cache"
      cp ${encoding} "$TMPDIR/data-gym-cache/${builtins.hashString "sha1" encodingUrl}"
    '';
  };
}
