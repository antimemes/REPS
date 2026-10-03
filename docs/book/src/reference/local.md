# Local execution and run storage

Use the generated experiment launcher for terminal runs.

## Choose where results are saved

Experiment launchers, publishing and verification use the same data-directory convention.

| Setting | Effect |
| --- | --- |
| `--data-dir DIR` | Selects storage for terminal experiments, publishing and verifier run-ID lookup; overrides the environment. |
| `ADB_DATA_DIR` | Default for terminal experiments and run-management commands. |
| No explicit directory | Uses `$XDG_DATA_HOME/adb` when `XDG_DATA_HOME` is set; otherwise `~/.local/share/adb`. |

For example, set this in every terminal used for the session before launching an experiment or inspecting its runs:

```sh
export ADB_DATA_DIR="$HOME/adb-first-run"
```

Changing the directory selects a different collection of runs; it does not move existing data. To inspect a run directly, use the `store` path printed by the terminal experiment. Under the chosen data directory:

- `runs/<condition_id>-<experiment>/<run_id>/run.json` records run metadata and outcome.
- `runs/<condition_id>-<experiment>/<run_id>/events.jsonl` contains the recorded events, one stream per run.
- `runs/<condition_id>-<experiment>/<run_id>/workspace/` is the experiment's working directory.

Condition identity, experiment, source and params live on each card and `run.start`.
Readers derive conditions by grouping cards on `condition`.

## Run the hello test in a terminal

Run this command as written for the default data directory, or set `ADB_DATA_DIR` first or append `--data-dir DIR` to choose another directory.

```bash
nix run .#inspect-hello -- \
  --set model=mockllm/model \
  --set limit=0 --set epochs=1 --set 'generate_args={}'
```

To run your checkout's version of `inspect-hello`, select **local checkout** in the command settings and run from that directory. The named experiment app handles terminal execution. Every declared parameter must be supplied. This is a credential-free check using fixed mock replies. It executes one run with both samples (`limit=0`), one pass (`epochs=1`), and no generation overrides. Expect two completed samples, zero errors, and score `1.0`. It needs no credentials or network access after the Nix build.

The runner prints a run ID and a `store` path. Read `run.json` and `events.jsonl` in that directory to inspect the run's metadata and events.

Use `--set KEY=VALUE` repeatedly to change parameters, or `--json` to stream event envelopes to the launcher’s standard output while retaining saved run data. Add `--non-interactive` to disable prompts for unattended execution. `--seed N` sets the run seed unchanged, in `0..2147483647` (random when omitted). Each invocation executes one run. `--dry-run` prints the resolved configuration without executing, and `--describe` prints the experiment's parameter schema.

## How do I read the files without a browser?

Start with the **store** path printed by the runner. To locate an older run, look under `runs/CONDITION_ID-EXPERIMENT/RUN_ID/` in your [data directory](layout.md#where-are-runs-saved). Its `run.json` identifies the experiment, condition, source reference and state. The card also contains `inputs.params` and `provenance.source`, copied from `run.start`. Readers derive conditions by grouping cards on `condition`; shared fields come from any member.

With Python 3, this example prints the metadata and reads result and completion events. Replace `/path/to/run` with that run's directory:

```sh
python3 - /path/to/run <<'PYTHON'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
print(json.dumps(json.loads((run_dir / "run.json").read_text()), indent=2))
for line in (run_dir / "events.jsonl").read_text().splitlines():
    envelope = json.loads(line)
    event = envelope["event"]
    if event.get("type") in {"result", "run.end"}:
        print(json.dumps(envelope))
PYTHON
```

Use this example on a finished run; a live file can end with a line still being written. For live processing, use the runner's [`--json` stream](../running/experiments.md#how-do-i-inspect-the-result). Preserve the envelope's run ID and sequence number when combining records. Other event types contain model calls, progress, logs and experiment-specific observations.

The [file reference](layout.md) and [event reference](events.md) define the fields. Check completion, errors and the evidence behind summary metrics before using a result in an analysis; matching condition IDs alone do not establish scientific comparability.
