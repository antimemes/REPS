"""A full-run scan must catch leaks outside JSONL as well as escaped records."""

import json

import pytest

from reps_testing import assert_run_has_no_secrets


@pytest.mark.parametrize("name", [
    "PROVIDER_API_KEY", "ACCESS_TOKEN", "SERVICE_SECRET", "PASSWORD",
    "CREDENTIAL_FILE", "lowercase_token",
])
def test_environment_credentials_are_checked_without_exposing_values(tmp_path, monkeypatch, name):
    secret = "sentinel-for-environment-scan"
    monkeypatch.setenv(name, secret)
    (tmp_path / "run.json").write_text(json.dumps({"nested": {"value": secret}}))
    with pytest.raises(AssertionError, match=name) as failure:
        assert_run_has_no_secrets(tmp_path)
    assert secret not in str(failure.value)


@pytest.mark.parametrize("relative", [
    "events-00000.jsonl", "events-00001.jsonl", "run.json",
    "workspace/nested/config.yaml", "workspace/.logs/debug.log", "artifacts/report.html",
])
def test_every_file_is_scanned(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = "fixture-file-secret"
    path.write_text(json.dumps({"value": secret}) + "\n")
    with pytest.raises(AssertionError, match="environment credential"):
        assert_run_has_no_secrets(tmp_path, environment={"TEST_SECRET": secret})


@pytest.mark.parametrize("leak, category", [
    ("sk-12345678", "API key"),
    ("sk-proj-" + "a" * 40, "API key"),
    ("sk-abcdEFGH1234", "API key"),
    ('"api_key":"sk-12345678"', "API key"),
    (" \tsk-12345678", "API key"),
    ("Bearer " + "a" * 24, "Bearer credential"),
    ("Bearer\n" + "a" * 24, "Bearer credential"),
    ("https://user:password@host.invalid/path", "URL userinfo"),
    ("ftp://user@host.invalid", "URL userinfo"),
    ("http://:password@[::1]:8080", "URL userinfo"),
    ("//user:password@host.invalid/path", "URL userinfo"),
])
@pytest.mark.parametrize("suffix", ["txt", "jsonl"])
def test_patterns_detect_credentials_absent_from_environment(tmp_path, leak, category, suffix):
    (tmp_path / f"output.{suffix}").write_text(
        json.dumps({"nested": [leak]}) if suffix == "jsonl" else leak,
    )
    with pytest.raises(AssertionError, match=category) as failure:
        assert_run_has_no_secrets(tmp_path, environment={})
    assert leak not in str(failure.value)


@pytest.mark.parametrize("suffix", ["json", "jsonl"])
@pytest.mark.parametrize("secret", ["fixture-\N{SNOWMAN}-secret", 'fixture-"quoted"\nsecret'])
def test_json_escaping_cannot_hide_environment_credentials(tmp_path, suffix, secret):
    path = tmp_path / f"events-00000.{suffix}"
    path.write_text(json.dumps({"nested": [{"value": secret}]}))
    assert secret not in path.read_text()
    with pytest.raises(AssertionError, match="environment credential"):
        assert_run_has_no_secrets(tmp_path, environment={"TEST_KEY": secret})


def test_json_escaping_cannot_hide_userinfo(tmp_path):
    (tmp_path / "events-00000.jsonl").write_text(
        '{"endpoint":"https:\\/\\/user:password\\u0040host.invalid"}\n',
    )
    with pytest.raises(AssertionError, match="URL userinfo"):
        assert_run_has_no_secrets(tmp_path, environment={})


def test_binary_files_are_scanned_as_text_too(tmp_path):
    (tmp_path / "artifact.bin").write_bytes(b"\xff\x00sk-12345678\x00\xfe")
    with pytest.raises(AssertionError, match="API key"):
        assert_run_has_no_secrets(tmp_path, environment={})


def test_clean_run_ignores_empty_secrets_and_noncredential_environment(tmp_path):
    text = "https://host.invalid/path@name?email=user@example.org ordinary"
    (tmp_path / "events-00000.jsonl").write_text(json.dumps({"text": text}) + "\n")
    (tmp_path / "artifact.bin").write_bytes(b"\xff\x00\xfe")
    assert_run_has_no_secrets(tmp_path, environment={"TEST_KEY": "", "PLAIN_VALUE": "ordinary"})


@pytest.mark.parametrize("text", [
    "risk-management",
    "risk-adjusted",
    "Storage name: dummy-jcpqcesk-jcpqcesk",
])
@pytest.mark.parametrize("suffix", ["txt", "jsonl"])
def test_key_pattern_does_not_match_inside_prose(tmp_path, text, suffix):
    (tmp_path / f"output.{suffix}").write_text(
        json.dumps({"text": text}) if suffix == "jsonl" else text,
    )
    assert_run_has_no_secrets(tmp_path, environment={})


@pytest.mark.parametrize("text", [
    "the bearer of long-term consequences",
    "bearer bonds",
    "Bearer token",
    "Bearer abc",
])
@pytest.mark.parametrize("suffix", ["txt", "jsonl"])
def test_bearer_pattern_ignores_prose_and_short_values(tmp_path, text, suffix):
    (tmp_path / f"output.{suffix}").write_text(
        json.dumps({"text": text}) if suffix == "jsonl" else text,
    )
    assert_run_has_no_secrets(tmp_path, environment={})


def test_missing_directory_is_not_a_successful_scan(tmp_path):
    with pytest.raises(AssertionError, match="existing run directory"):
        assert_run_has_no_secrets(tmp_path / "missing", environment={})


def test_pytest_failure_output_does_not_echo_the_secret(pytester, monkeypatch):
    secret = "fixture-only-do-not-echo-this-value"
    monkeypatch.setenv("SCANNER_TEST_KEY", secret)
    run = pytester.path / "run"
    run.mkdir()
    (run / "output.txt").write_text(secret)
    pytester.makepyfile('''
from reps_testing import assert_run_has_no_secrets

def test_leaked_run():
    assert_run_has_no_secrets("run")
''')
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(failed=1)
    output = result.stdout.str() + result.stderr.str()
    assert "SCANNER_TEST_KEY" in output
    assert secret not in output
