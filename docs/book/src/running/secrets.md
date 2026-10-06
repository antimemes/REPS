# Configure model credentials

ADB stores model credentials and endpoints in named profiles on your machine. A run selects the credential sets referenced by its model inputs and passes the selected values to the experiment process. Exporting an API key in your shell does not configure an ADB run.

## How do I configure a provider?

Use the standalone credential-management command below.
Direct `adb-runner` invocation here configures profiles; experiment execution
uses the [named experiment app](experiments.md#how-do-i-use-the-command-line).

```sh
nix run .#adb-runner -- credentials set openai
```

Enter the requested values and profile name. Secret input is hidden. A bare set name offers `default` as the profile name; use an explicit name to edit or create a specific profile:

```sh
nix run .#adb-runner -- credentials set openai.research
```

The endpoint prompt offers the provider's configured default where one exists. Use the endpoint expected by the adapter and provider, including its scheme and API path. For self-hosted models, configure `vllm` with `VLLM_BASE_URL` (for example, `http://localhost:8000/v1`) or `ollama` with `OLLAMA_BASE_URL` (for example, `http://localhost:11434/v1`). Neither has a default endpoint; `VLLM_API_KEY` and `OLLAMA_API_KEY` are optional.

List saved sets and locate the file with:

```sh
nix run .#adb-runner -- credentials list
nix run .#adb-runner -- credentials path
```

The store is normally `~/.config/adb/credentials.toml`, or `$XDG_CONFIG_HOME/adb/credentials.toml`. `ADB_CREDENTIALS_FILE` overrides that path. ADB writes the file with mode `0600` and refuses regular credential files readable by group or others. The file contains plaintext credentials; keep it outside the run store and repository.

## Which profile will a run use?

Add `--credential openai=research` to an experiment command to select that profile explicitly. Repeat the flag for runs using several credential sets. The selected set must be used by this run, and the profile must exist.

Without an explicit selection, the runner uses a remembered choice for this experiment and set, then a lone default profile. In an interactive terminal it offers a picker when there are other profiles, and offers first-use setup for an unconfigured built-in provider. With `--non-interactive` or non-terminal stdin it never prompts: it uses the remembered choice or default profile, and fails if a required selection is unavailable.

You can save a preference explicitly:

```sh
nix run .#adb-runner -- credentials remember inspect-hello openai research
```

Preferences are stored separately in `$XDG_CONFIG_HOME/adb/preferences.toml`, defaulting to `~/.config/adb/preferences.toml`. An explicit `--credential` overrides them. Profiles are selected as a whole; missing fields are not filled from another profile.

## How do model IDs select credential sets?

Usually the part before the first slash is the set: `openai/MODEL` uses `openai`, and `vllm/Qwen/Qwen2.5-7B-Instruct` uses `vllm`. Configure a self-hosted service with:

```sh
nix run .#adb-runner -- credentials set vllm.default
```

That prompt uses `VLLM_API_KEY` and `VLLM_BASE_URL`; use `vllm/Qwen/Qwen2.5-7B-Instruct` as the model ID. For Ollama, configure `ollama.default` and use a library tag such as `ollama/qwen2.5:7b-instruct-fp16`. Profiles within each set select the venue; for example, `--credential vllm=cluster` selects `vllm.cluster`.

Inspect-style IDs of the form `openai-api/SERVICE/MODEL` use `SERVICE` as the credential set. Which model-ID forms are accepted depends on the experiment's adapter. An unknown prefix does not itself add provider support. Mock prefixes `mock` and `mockllm` require no credential set.

ADB discovers model IDs in `llm`-typed inputs, including nested lists and structures. It does not infer credentials from arbitrary string fields or free-form JSON objects.

## How do I update, remove or script profiles?

Run `credentials set SET.PROFILE` again to edit a profile. Remove one profile or an entire set with `credentials remove SET.PROFILE` or `credentials remove SET`.

For automation, `credentials set SET.PROFILE --json` reads one JSON object from standard input. Each key is an environment-variable name and each value is a string; `null` removes an existing field. Empty strings and omitted fields retain existing values. Feed that input from your secret-management system rather than putting secret values in command arguments. `credentials list --json` returns masked inventory for tools. See [command reference](../reference/cli.md#credential-commands).
