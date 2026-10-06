# Run your first local experiment

Use the terminal to run `inspect-hello`, a small evaluation with fixed mock replies. You need [Nix installed](https://nixos.org/download/) on Linux or macOS and an internet connection for the initial build. This example needs no model credentials.

## Inspect the inputs

Ask the experiment for its manifest:

```sh
nix run .#inspect-hello -- --describe
```

The commands on this page follow your [Nix command settings](nix.md), including downloaded source or a local checkout. The default uses classic Nix and requires no checkout.

The manifest describes every parameter and result. Each parameter must be supplied explicitly; an `initial` value is a suggestion, not a command-line default. For this example, choose `mockllm/model`, both samples (`limit=0`), one pass (`epochs=1`) and no generation overrides (`generate_args={}`).

## Check the command

```sh
nix run .#inspect-hello -- \
  --set model=mockllm/model --set limit=0 --set epochs=1 \
  --set 'generate_args={}' --dry-run
```

This validates the parameter names and types and prints the resolved inputs, condition ID and seed without executing the experiment. For a real model, [configure credentials](secrets.md) before running.

## Execute one run

Remove `--dry-run`:

```sh
nix run .#inspect-hello -- \
  --set model=mockllm/model --set limit=0 --set epochs=1 \
  --set 'generate_args={}'
```

The generated launcher builds the pinned program and starts the runner with its manifest and source identity. Expect two completed samples, zero errors and score `1.0`. Each invocation creates one run. Ctrl-C interrupts execution while retaining the partial record.

The terminal prints the run ID and **store** path. The default data directory is `~/.local/share/reps`, or `$XDG_DATA_HOME/reps` when set. `REPS_DATA_DIR` or `--data-dir DIR` selects another directory; see [run storage](../reference/local.md#choose-where-results-are-saved).

## Inspect the result

Open `run.json` in the printed run directory for inputs, provenance, state and derived results. `events.jsonl` contains the event envelopes, including each result and the final `run.end`. Follow the [terminal reading example](../reference/local.md#how-do-i-read-the-files-without-a-browser), or add `--json` to the experiment command to stream the events to standard output while they are saved.

A completed process does not establish that every evaluation item succeeded; check the recorded results and errors too. Before collecting real-model results, [audit the first run](model.md#how-do-i-audit-the-first-real-run) with `reps-runner verify`.
