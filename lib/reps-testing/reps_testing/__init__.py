"""Shared test support, discovered automatically by pytest when installed."""

from .secrets import assert_run_has_no_secrets

__all__ = ["assert_run_has_no_secrets"]
