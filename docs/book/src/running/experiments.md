# Choose inputs and run

Inspect an experiment’s manifest and bind its parameters in the terminal. Neither this nor the [first-run example](getting-started.md) requires a checkout.

## How do I use the command line?

Use the generated experiment launcher, which supplies the runner’s execution context. The command’s Nix form and source follow the [command settings](nix.md). A command using moving `main` runs that source’s current experiment; to repeat a historical configuration, [select its recorded source revision](model.md#how-do-i-keep-the-source-version) and inputs.

For a model that needs credentials, [configure a saved profile](secrets.md) and select it with `--credential SET=NAME` when needed. An interactive terminal can prompt for missing built-in credentials; noninteractive runs require setup beforehand.

Ask the experiment for its manifest:

```sh
nix run .#inspect-hello -- --describe
```

Supply each declared parameter with `--set`. This keyless example uses the bundled mock model:

```sh
nix run .#inspect-hello -- \
  --set model=mockllm/model \
  --set limit=0 \
  --set epochs=1 \
  --set 'generate_args={}'
```

Every parameter is required on the command line, including those with an `initial` value in the manifest. If a parameter is missing, the runner prints an example command. Review its values before running it.

Values can be JSON, a bare string or `@path` to read a file. Quote a whole `KEY=VALUE` argument when it contains spaces or shell punctuation. For example, replace the last argument above with `--set generate_args=@generation.json` after saving a JSON object in that file. An explicit `null` is allowed only for a parameter declared nullable. See [value syntax](../reference/cli.md#how-are-parameter-values-read).

## How do I check before executing?

Add `--dry-run` to a complete command. It checks parameter names and types and prints the condition and inputs without starting the experiment or resolving credentials. List-length bounds are checked only when executing. It does not test account access, model availability or an experiment's external dependencies.

Remove `--dry-run` when ready. The runner prints a run ID and a `store` path. Each invocation executes one run.

## How do I inspect the result?

Read `run.json` and `events.jsonl` in the printed run directory to inspect the saved metadata and events.

For terminal processing, add `--json` to the experiment command to stream event envelopes as JSON lines on the launcher’s stdout; its diagnostics go to stderr. This is distinct from the child experiment program’s stdout, which is captured as text events. Saved run files are still written. For unattended execution, also pass `--non-interactive` to disable prompts; required credentials and profile selections must be resolvable without input. Each envelope identifies its run.

Check `event.state` in each `run.end` envelope, or `lifecycle.state` in the saved `run.json`. The runner returns `0` when the run completes, `1` if it fails or times out, and `130` on interruption. A completed process can also report errors in individual evaluation items, so inspect the summary and relevant events. Missing `run.end` may mean execution is still active or stopped before recording its outcome. The [terminal reading example](../reference/local.md#how-do-i-read-the-files-without-a-browser) shows how to inspect the saved record.

To interrupt a terminal run, press Ctrl-C. Inspect the retained record to see how far it progressed. To run the same configuration again, use the original command; [repeat and compare runs](model.md) explains seeds and source versions.
