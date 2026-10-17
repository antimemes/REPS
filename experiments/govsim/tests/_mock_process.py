"""A fresh interpreter warms upstream one time, then forks an isolated case for each request.

Do not fork the pytest worker: event_capture has a receiver thread. This parent
does no simulation work and uses one numeric thread before forking; each child
runs main(), with its thread settings, seed and environment.
"""

import contextlib
import importlib
import json
import multiprocessing
import os
import sys
import tempfile
import warnings


def run_case(request, stdout, stderr):
    os.dup2(stdout, 1)
    os.dup2(stderr, 2)
    os.environ.clear()
    os.environ.update(request["env"])
    os.chdir(request["cwd"])
    sys.argv = request["argv"]
    exec(compile(request["script"], "<string>", "exec"), {"__name__": "__main__"})


def serve():
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["WANDB_MODE"] = "disabled"
    # Match main's suppression while importing wandb's generated GraphQL models.
    # The cold bootstrap test keeps coverage of warnings during real startup.
    from pydantic.warnings import UnsupportedFieldAttributeWarning
    warnings.filterwarnings("ignore", category=UnsupportedFieldAttributeWarning)
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        torch.set_num_threads(1)
        for scenario in ("fishing", "sheep", "pollution"):
            importlib.import_module(f"simulation.scenarios.{scenario}.run")
    assert "sentence_transformers" in sys.modules
    print(json.dumps({"ready": True}), flush=True)
    context = multiprocessing.get_context("fork")
    for line in sys.stdin:
        request = json.loads(line)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            child = context.Process(target=run_case, args=(request, stdout.fileno(), stderr.fileno()))
            child.start()
            child.join(120)
            timed_out = child.is_alive()
            if timed_out:
                child.kill()
                child.join()
            stdout.seek(0)
            stderr.seek(0)
            print(json.dumps({"returncode": child.exitcode, "timed_out": timed_out,
                              "stdout": stdout.read().decode(), "stderr": stderr.read().decode()}), flush=True)


if __name__ == "__main__":
    serve()
