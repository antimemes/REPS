"""Isolated mock cases share only imports, not mutable simulation state."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile

import pytest


@pytest.fixture(scope="module")
def warmed_run():
    with tempfile.TemporaryFile(mode="w+t") as startup_errors:
        helper = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("_mock_process.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=startup_errors,
            text=True, start_new_session=True,
        )

        def receive():
            if not select.select([helper.stdout], [], [], 150)[0]:
                pytest.fail("warmed mock helper did not respond within 150 seconds")
            line = helper.stdout.readline()
            if not line:
                startup_errors.seek(0)
                pytest.fail("warmed mock helper exited: " + startup_errors.read())
            return json.loads(line)

        def run(script, config, *, cwd, env):
            helper.stdin.write(json.dumps({"script": script, "argv": ["-c", str(config)],
                                           "cwd": str(cwd), "env": env}) + "\n")
            helper.stdin.flush()
            result = receive()
            args = [sys.executable, "-c", script, str(config)]
            if result["timed_out"]:
                raise subprocess.TimeoutExpired(args, 120, result["stdout"], result["stderr"])
            return subprocess.CompletedProcess(args, result["returncode"], result["stdout"], result["stderr"])

        try:
            assert receive() == {"ready": True}
            yield run
        finally:
            helper.stdin.close()
            try:
                helper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(helper.pid, signal.SIGKILL)
                helper.wait()
            helper.stdout.close()
