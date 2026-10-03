# Add or change an experiment

Experiments live under `experiments/` in this repository. Each directory has a `package.nix` that returns one or more named experiments. The package registry imports those directories automatically; you do not need to add an entry to the root flake.

To add an experiment, follow the small keyless example below and check the complete integration before introducing external dependencies. To extend one already in the repository, go to [change an existing experiment](#how-do-i-change-an-existing-experiment).

## How do I prepare the checkout?

Clone the repository and enter its development shell:

```sh
git clone https://github.com/antimemetics-institute/agentdatabank.git
cd agentdatabank
nix-shell
```

Flake users can enter the same shell with `nix develop`. Create `experiments/example-count/` for the new experiment. Use a unique experiment name; the registry rejects duplicates across directories.

## How do I declare the experiment?

Save this as `experiments/example-count/package.nix`:

```nix
{ adb, writeShellApplication, jq, adb-runner }:
let
  program = writeShellApplication {
    name = "example-count-program";
    runtimeInputs = [ jq adb-runner ]; # the runner's environment includes adb-events' CLI
    text = ''
      count="$(jq -er '.count')"
      adb-emit result --name count --value "$count"
    '';
  };
in
{
  example-count = adb.mkExperiment {
    name = "example-count";
    summary = "Record an explicitly supplied count.";
    src = ./.;
    inherit program;
    params = with adb.types; {
      count = param int {
        description = "Integer to record in the run results.";
        initial = 3;
      };
    };
    results = [
      {
        name = "count";
        type = adb.types.int;
        label = "Recorded count";
        description = "The supplied count echoed by the program.";
        details = "This checks the integration, not model performance.";
      }
    ];
  };
}
```

`params` supplies validation and command suggestions. `initial` supplies a suggested value; users must still bind the parameter explicitly when running from the CLI. `results` declares possible summary metrics and explains their meaning. See [manifest reference](../reference/manifest.md#result-declarations) for the result fields. Declaration list order sets the definitions order.

Give each result a readable `label` and a short, plain-language `description` explaining what the value means. Use optional `details` for the calculation, aggregation and interpretation caveats. Add `unit` when useful. Explain what a boolean means in the experiment, including whether it represents success or failure. Distinguish observed progress from configured limits, and explain special cases such as an equality score of 1 when every agent gained zero.

These definitions describe possible outputs, not required outputs: an absent metric is missing, not zero. Undeclared results remain in the stream, warn, and are excluded from the summary; repeated declared results warn and use the last value. The runner saves the definitions in `run.json` under `definitions.results` and in `run.start` as `result_definitions`, so readers retain the explanation used when the run began.

`src` declares the experiment's identity sources. Include code, configuration and dependency locks that determine this experiment's behavior. A path is usual; use a list when the experiment depends on several source trees. Changes anywhere in those declared inputs can change the condition identity, including a README inside a declared directory. Development artifacts such as `.venv` are filtered out. The [identity reference](../reference/layout.md#how-is-a-condition-id-calculated) gives the exact rule.

## What must the program do?

`adb.mkExperiment` generates the named experiment launcher. Authors supply the
underlying `program`; users invoke the named app.
The generated launcher provides the manifest, source identity, and program path
to the runner. Do not invoke `adb-runner`, build its execution environment by
hand, or start the underlying program directly to create a run.

The runner starts the program in a fresh workspace, sends the complete parameter object as JSON on standard input, and provides the run directory and seed in environment variables. Use `adb_events.emit()` in Python or the `adb-emit` CLI in other languages. The shell example reads its input with `jq` and calls `adb-emit` to record a result matching its declared result name. The CLI belongs to `adb-events`; this shell example obtains it through the runner's environment, and never invokes the runner itself. Ordinary stdout/stderr is captured as text, including printed JSON.

For a larger program, put the implementation beside `package.nix` and have the adapter invoke its packaged executable. Pin dependencies in the package definition and dependency lock. Python experiments can use `adb.mkPythonEnv` to build a `pyproject.toml`/`uv.lock` workspace, and `adb-events` for validated event emission. `adb-experiment` provides a shared parameter-reading scaffold and artifact helper; see its [failure behavior](../reference/protocol.md#how-can-python-experiments-emit-validated-events) before adopting it.
Each call to `adb.mkPythonEnv` must declare `python` explicitly, for example
`python = pkgs.python313;`; the helper has no interpreter default.

Both emitter APIs handle validation and transport. Do not write a socket client in an experiment.

Add [standard events](../reference/events.md) for the evidence a reader needs: model calls and results. Use custom events for messages and instance outcomes. Preserve useful native output as artifacts, write files under `ADB_RUN_DIR/artifacts/`, and emit pointers under an experiment-specific custom kind. Read `ADB_SEED` and pass it to supported random generators or backend settings. The [process protocol](../reference/protocol.md) defines the boundary in full.

## Instrumenting an experiment

Enumerate everything upstream writes before writing the translator: logs, per-agent
files, checkpoints and auxiliary data. GovSim's [ingestion module](../../../../experiments/govsim/govsim_adapter/upstream.py)
captures the environment log and each persona's memory nodes, including checkpoints
left by a failed simulation.

Keep foreign rows as open JSON dictionaries; never narrow them to the fields a
current analysis uses. GovSim's [native row models](../../../../experiments/govsim/govsim_adapter/models.py)
retain unknown keys, nested values and explicit nulls while identifying known actions
with typed custom kinds.

Mark each ingested source before its records with its relative path, byte size,
SHA256 of the original bytes and emitted record count. GovSim's
[file markers](../../../../experiments/govsim/govsim_adapter/upstream.py)
let readers check which checkpoint contributed each sequence of rows.

Leave out only what is recomputable, and state the inputs needed to reproduce it.
GovSim's [adapter](../../../../experiments/govsim/govsim_adapter/main.py)
omits embeddings because its recorded node descriptions and the embedder named in
`govsim.config` reproduce them.

Use upstream's own actor IDs and declare a roster for display labels. GovSim's
[config render hint](../../../../experiments/govsim/govsim_adapter/models.py)
maps `persona_N` to the configured name, so even a persona that never speaks has
a label on its model calls and memory rows.

Attribute model calls to the component that made them. GovSim's
[logger hook](../../../../experiments/govsim/govsim_adapter/logger.py)
uses persona IDs for persona calls and `framework/<component>` for framework
queries, preserving phase and query context as producer metadata.

Declare results and their meanings in the manifest, then calculate and emit them
in the adapter. GovSim's [result declarations](../../../../experiments/govsim/package.nix)
set their display order and definitions; its adapter computes the scientific
quantities. Readers can use the emitted results without deriving experiment-specific
metrics from native rows.

Run the reusable [secrets scan](../../../../lib/adb-events/adb_events/secrets.py)
from `adb_events.secrets` (also re-exported by `adb_testing`) on the entire run
directory with fake credentials seeded into the environment. GovSim's
`test_resolved_config_copies_no_secrets` in [test_adapter.py](../../../../experiments/govsim/tests/test_adapter.py)
checks captured events and every workspace file, so a resolved config or raw request cannot
silently copy those credentials into publishable data.

### Upstreams without Python packaging

For an upstream with no packaging metadata, add it with a patch and install the
package into the experiment's venv. GovSim's
[packaging.patch](../../../../experiments/govsim/packaging.patch) adds a minimal
`pyproject.toml` that includes only `simulation` and its YAML configuration data.
Its [package declaration](../../../../experiments/govsim/package.nix) defines the
pinned upstream in the `overrides` overlay and selects it with `extraPackages`.
Keep this packaging inline until another experiment needs the same mechanism.
Address installed data through `importlib.resources` or the framework's package
provider, never through a checkout path or an environment variable. Keep patches
split: `seed.patch` edits existing upstream files and behavior; `packaging.patch`
adds missing packaging files without changing behavior.

`package.nix` declares the experiment and never declares tests. Put checks in
`tests/default.nix`, outside the experiment's identity sources. Like nixpkgs'
`testers.*`, the shared `adb.testers` builders take test data and return ordinary
derivations; the registry attaches them as `passthru.tests` and discovers them
for `nix flake check` and `task test:python`.

```nix
{ experiment, adb }: {
  pytest = adb.testers.pytest { inherit experiment; tests = ./.; };
  smoke = adb.testers.smoke {
    inherit experiment;
    params = {
      experiment = "fish_baseline_concurrent";
      model = "mock/model";
      embedder = "hash";
      max_rounds = 1;
      max_tokens = 8000;
      threads = 2;
      reasoning_effort = null;
      temperature = 0.0;
      top_p = 1.0;
    };
  };
}
```

`pytest` selects the Python program's `dev` group in its canonical Nix venv.
Pass the test directory explicitly as `tests = ./.`; `flags` adds pytest arguments.
Project test suites use `pytest-xdist` with `-n auto`; the Nix tester selects eight
workers to bound concurrent numeric imports. Use `uv run pytest -n 0` when
debugging a hang, or `uv run pytest -o addopts='' -p no:xdist` to disable the plugin
entirely. In a Nix check, `flags = [ "-n" "0" ];` selects serial execution.
Environment knobs such as `HF_HUB_OFFLINE` belong to the experiment: set `env` and
`preCheck` in its `tests/default.nix`, like nixpkgs check hooks. `overrideAttrs`
is the escape hatch. For example, Concordia's program is a shell adapter, so its
[test declaration](../../../../experiments/concordia/tests/default.nix) supplies
an explicit Python test venv through `nativeBuildInputs`. Nixpkgs `testers.*` are
also fine for checks the two ADB testers do not cover.

`smoke` takes the complete condition in `params`, runs the built launcher, and
verifies its completed store. The oneliner IS the condition spec for a smoke run
too: no defaults are hidden in the tester. Adding a parameter means adding it to
the smoke check, just as to every oneliner and sweep script. Manifest initials
are independent of the smoke condition. The tester does not seed credentials;
testing whether an adapter copies environment secrets is an adapter unit-test
concern. Use `overrideAttrs` check hooks for offline fixtures,
as in inspect-hello's [tokenizer cache](../../../../experiments/inspect_evals/tests/default.nix).
For GovSim's focused loop, run `nix build .#checks.x86_64-linux.govsim -L` (using
your machine's system).

## How do I check the integration?

Keep the three test layers separate:

- `experiments/*/tests` uses `adb-testing`: `event_capture` and
  `assert_run_has_no_secrets` check that the adapter emits the right events, in
  order, without secrets. Experiment tests never import `adb_runner`.
- `runner/tests` uses small fixture programs to check that the runner executes
  anything that speaks the producer protocol.
- Each experiment's `passthru.tests.smoke` runs its **built launcher** on a mock
  configuration, then `adb-runner verify` on the completed store. This is the
  check that the runner executes that registered experiment offline, writes
  conforming evidence and a matching card, and leaks nothing.

Keep smoke parameters in the experiment's check, not in the shared declaration
API. The smoke tester runs with `--non-interactive`, asserts that exactly one run
completed, and passes that experiment's manifest directly to `adb-runner verify`.
Verification checks that reported result names equal the manifest's declarations
and reports missing or extra names. The separate completion check matters because
`verify` can also audit failed or interrupted runs.
See GovSim's [checks](../../../../experiments/govsim/tests/default.nix) and
Concordia's [test wiring](../../../../experiments/concordia/tests/default.nix).
Keep seeded-credential scans in adapter tests; verify real runs against the real
credentials before publishing.
`nix flake check -L` discovers these checks through the registry; `task ci` uses
that same entry point. No test-specific runner dependency belongs in an
experiment's development group.

For local flakes, stage the new files so Nix includes them:

```sh
git add experiments/example-count
```

From the repository root, inspect the schema and validate inputs:

```sh,repo-local
nix run .#example-count -- --describe
nix run .#example-count -- --set count=3 --dry-run
```

Then execute the keyless example through its generated named app:

```sh,repo-local
nix run .#example-count -- --set count=3
```

Inspect `run.json` and `events.jsonl` in the printed run directory and verify that the input and result are both `3`. The [terminal reading example](../reference/local.md#how-do-i-read-the-files-without-a-browser) shows how.

For a real experiment, test parameter rejection, event shapes, summary selection and failure reporting with mocks or small local fixtures. Run the affected package's tests; after changing dependency declarations, update its lock and check dependent locks. `task lock:check` checks lock freshness, and `task ci` runs the repository's broader checks. Build documentation separately with `task docs:build` when changing it.

## How do I test Python emission without launching a full run?

Add `adb-testing` to your experiment's development dependencies and regenerate
its lock. From `experiments/EXPERIMENT/pyproject.toml`, the local source is:

```toml
[dependency-groups]
dev = ["pytest>=8", "pytest-xdist", "adb-testing"]

[tool.uv.sources]
adb-testing = { path = "../../lib/adb-testing", editable = true }
```

Merge these entries into existing sections. Pytest discovers the installed
`adb-testing` plugin automatically; no `conftest.py` registration is needed.
Tests that request `event_capture` receive the contract's reference socket receiver and its
connection environment; the fixture cleans up afterward. It is not autouse:
parameter-validation tests that do not emit events need not request it. Do not
implement a test socket server or patch emission to print JSON.

Call your adapter normally, then inspect `event_capture.read()`. For example,
this shows the fixture interface; in an adapter test, replace the direct `emit`
call with the adapter function being tested:

```python
from adb_events import Result, emit


def test_recorded_score(event_capture):
    emit(Result(name="score", value=1))
    events = event_capture.read()
    assert any(e["type"] == "result" and e["name"] == "score" and e["value"] == 1
               for e in events)
```

`read()` returns and clears the captured payload dictionaries. Assertions on
ordinary prints still use pytest's `capsys` separately. Subprocess tests inherit
the fixture's environment; preserve it when constructing a child environment.
This fixture checks emission and delivery, not run orchestration or stored
summaries. Check the complete integration through the named app as above.

## How do I change an existing experiment?

Find its declaration in `experiments/` and the adapter that consumes its inputs. Read the experiment's README and tests before editing.

For example, suppose an adapter currently calls its model with a hardcoded `temperature=0.5`. To let readers choose that value while retaining the previous behavior:

1. Capture the existing behavior with a small fixture that records the model request and emitted results. Keep its inputs and source revision so you can compare after editing.
2. Add a parameter to that experiment's `params` declaration:

   ```nix
   temperature = param float {
     description = "Sampling temperature passed to the model.";
     initial = 0.5;
   };
   ```

3. Add the field to the adapter's parameter schema, including any backend constraints, and replace the hardcoded argument with the supplied value. For a Python adapter with a validated `params` object, the call becomes `temperature=params.temperature`.
4. Update the README commands and fixtures to supply `--set temperature=0.5` alongside every existing parameter. `initial` supplies a suggested value; it does not make this new CLI argument optional. Existing commands must be updated.
5. Run the before/after fixture with `0.5` and check that the model request and relevant results match. Then use a different value, such as `0.2`, and assert that it reaches the backend. A mock that ignores temperature can check wiring but cannot establish how a real model responds. Check invalid inputs and inspect a small run's parameters, events and summary.

Preserving behavior does not preserve fingerprints: changes to declared source content or the parameter set can change the condition ID. An explicit `--seed` stays unchanged across these edits. Use a fixed seed when testing behavior; see [seeds](../running/model.md#how-do-seeds-work).

If the change makes the experiment incompatible with its earlier meaning or behavior, give it a separate experiment identity and document the distinction. There is no agreed folder-suffix convention or general experiment-versioning system.

## What belongs in the pull request?

Add a `README.md` in the experiment directory. Only Markdown is supported. Explain the research question, upstream source, required services, input meanings, result interpretation and a small runnable example. Keep experiment-specific usage there rather than adding a catalog page to this book.

Describe what the new experiment or change does, the evidence it records, and the checks you ran. Include updated locks and fixtures needed to reproduce those checks. Submit the code and documentation through an ordinary repository pull request.
