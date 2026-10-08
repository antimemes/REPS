# REPS

[![docs](https://img.shields.io/badge/docs-book-blue)](https://antimemes.github.io/REPS/) [![databank](https://img.shields.io/badge/databank-databank.antimemetics.institute-blue)](https://databank.antimemetics.institute)

REPS packages replications of multi-agent safety experiments, runs them on your machine, and saves their inputs, results and execution records together. This repository contains the experiments, runner, Nix library and data-format documentation.

## Run an experiment

With [Nix installed](https://nixos.org/download/) on Linux or macOS, run a credential-free test without cloning the repository:

```sh
$(nix-build --no-out-link --tarball-ttl 0 \
  https://github.com/antimemes/REPS/archive/main.tar.gz \
  -A exec.inspect-hello) \
  --set model=mockllm/model --set limit=0 --set epochs=1 --set 'generate_args={}'
```

The first build downloads the pinned dependencies. The example binds every parameter exposed by the experiment's `--describe` output and uses fixed mock replies. It runs both samples once and should finish with score `1.0`. The runner prints the run ID and the directory containing its metadata and events.

The [first-run guide](https://antimemes.github.io/REPS/running/getting-started.html) explains how to inspect parameters, validate a command and read its results.

## What would you like to do next?

- **[Choose inputs and run](https://antimemes.github.io/REPS/running/experiments.html):** inspect the manifest and supply every declared parameter.
- **[Read saved results](https://antimemes.github.io/REPS/reference/local.html#how-do-i-read-the-files-without-a-browser):** inspect the stored JSON and event files.
- **[Repeat and compare runs](https://antimemes.github.io/REPS/running/model.html):** keep track of source versions, inputs and seeds, and check the evidence behind differences.
- **[Add or change an experiment](https://antimemes.github.io/REPS/authoring/experiments.html):** clone the repository, edit and test locally, then contribute the code through a pull request.

The [book](https://antimemes.github.io/REPS/) also covers credentials, Nix command options, and reference details for commands, manifests, run files and events. See the [roadmap](https://antimemes.github.io/REPS/start/roadmap.html) for planned work.
