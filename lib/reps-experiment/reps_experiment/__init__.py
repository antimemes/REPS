"""reps-experiment: the Python experiment-side toolkit for REPS.

The runner-protocol scaffold re-exported here; provider routing in
:mod:`reps_experiment.providers`; the instrumented LLM client in
:mod:`reps_experiment.llm` (imported explicitly — it needs the ``[llm]`` extra's
OpenAI SDK, which the base package deliberately doesn't require).
"""

# explicit re-exports (`x as x`): the typed-package idiom both pyright and ruff
# understand, replacing the noqa that only ruff could read
from .scaffold import deposit_artifact as deposit_artifact
from .scaffold import experiment_main as experiment_main
