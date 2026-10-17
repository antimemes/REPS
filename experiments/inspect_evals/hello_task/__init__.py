"""A trivial two-sample task: say hello to your model.

Instruction-following samples scored with `includes()`, chosen to let BOTH types
of model pass deterministically: each real instruct model includes the requested
word, and the targets are also substrings of mockllm's fixed reply ("Default
output from mockllm/model") — thus `--set model=mockllm/model` runs the same task
keyless and offline (CI, no-key smoke) with the same 1.0 score. No dataset
download either way.
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import generate


@task
def hello() -> Task:
    return Task(
        dataset=MemoryDataset([
            Sample(input='Include the word "model" somewhere in your reply.',
                   target="model"),
            Sample(input='Include the word "output" somewhere in your reply.',
                   target="output"),
        ]),
        solver=generate(),
        scorer=includes(),
    )
