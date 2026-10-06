"""The same full-run secrets scan used by ``reps-runner verify``."""

from reps_events.secrets import assert_run_has_no_secrets as assert_run_has_no_secrets

__all__ = ["assert_run_has_no_secrets"]
