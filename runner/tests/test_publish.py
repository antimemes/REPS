"""Real verification, boto3 profile resolution and S3 semantics, with no live service."""

import json
import shutil
import sys

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
from moto import mock_aws
import pytest
import zstandard

from adb_events import read_events
from adb_runner import cli
from adb_runner.card import derive_card
from adb_runner.publish import Publisher, PublishError, parse_target
from test_verify import saved, digest_files, rewrite_stream


@pytest.fixture
def bucket(tmp_path, monkeypatch):
    for name in list(__import__('os').environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    config = tmp_path / "aws-config"
    credentials = tmp_path / "aws-credentials"
    config.write_text("[profile throwaway]\nregion = us-east-1\n")
    credentials.write_text("[throwaway]\naws_access_key_id = testing-access\naws_secret_access_key = testing-secret\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_PROFILE", "throwaway")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws(config={"core": {"mock_credentials": False}}):
        s3 = boto3.Session(profile_name="throwaway").client("s3")
        s3.create_bucket(Bucket="throwaway")
        yield s3
    assert config.read_text() == "[profile throwaway]\nregion = us-east-1\n"
    assert credentials.read_text().endswith("aws_secret_access_key = testing-secret\n")


@pytest.fixture
def publication(saved, tmp_path, monkeypatch):
    directory, manifest = saved
    # Publishable evidence records a pinned revision in both the stream and card.
    rewrite_stream(directory, lambda rows: rows[0]["event"].update(fetch_ref="github:owner/repo/" + "a" * 40))
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    home = tmp_path / "data dir's"
    directory.parents[2].rename(home)
    directory = home / "runs" / directory.parent.name / directory.name
    monkeypatch.setenv("ADB_MANIFEST", str(manifest))
    monkeypatch.setenv("ADB_DATA_DIR", str(tmp_path / "wrong-store"))
    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["adb-runner", "publish", "--to", "s3://throwaway/prefix",
            "--data-dir", str(home), *args])
        return cli.main()
    return directory, invoke


def objects(s3):
    return {row["Key"]: s3.get_object(Bucket="throwaway", Key=row["Key"])["Body"].read()
            for row in s3.list_objects_v2(Bucket="throwaway").get("Contents", [])}


def test_publish_refuses_failed_model_observations_before_upload(publication, bucket, capsys):
    directory, invoke = publication

    def insert(rows):
        rows.insert(-1, {**rows[0], "event": {
            "type": "llm.call", "model": "mock/model", "input": [], "output": {},
            "error": "Request timed out.",
        }})
        for seq, row in enumerate(rows):
            row["seq"] = seq

    rewrite_stream(directory, insert)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    before = digest_files(directory)
    assert invoke("--profile", "throwaway") == 1
    assert "failed model calls: 1; first error: Request timed out." in capsys.readouterr().err
    assert objects(bucket) == {}
    assert digest_files(directory) == before


def clone(directory, number, *, experiment="t", state="completed"):
    target = directory.parents[1] / f"cid-{experiment}" / f"20260916t120000z-{number:012x}"
    shutil.copytree(directory, target)
    def update(rows):
        for row in rows:
            row.update(run=target.name, experiment=experiment)
        rows[-1]["event"].update(state=state, exit_code=0 if state == "completed" else 1)
    rewrite_stream(target, update)
    (target / "run.json").write_text(json.dumps(derive_card(read_events(target))))
    return target


def test_publish_preserves_records_and_refuses_overwrite_per_run(publication, bucket, capsys):
    directory, invoke = publication
    before = digest_files(directory.parents[2])
    assert invoke("--profile", "throwaway") == 0
    result = objects(bucket)
    base = f"prefix/runs/{directory.parent.name}/{directory.name}"
    assert zstandard.ZstdDecompressor().decompress(result[f"{base}/events.jsonl.zst"]) == (directory / "events.jsonl").read_bytes()
    decoder = zstandard.ZstdDecompressor().decompressobj()
    decoder.decompress(result[f"{base}/events.jsonl.zst"])
    assert decoder.eof and not decoder.unused_data  # exactly one frame
    assert result[f"{base}/run.json"] == (directory / "run.json").read_bytes()
    headers = bucket.head_object(Bucket="throwaway", Key=f"{base}/events.jsonl.zst")
    assert headers["ContentType"] == "application/zstd" and "ContentEncoding" not in headers
    assert not any("workspace/" in key or "conditions/" in key for key in result)
    assert set(result) == {f"{base}/events.jsonl.zst", f"{base}/run.json"}
    assert digest_files(directory.parents[2]) == before
    # Overwrite refusal is per run: a later run still uploads.
    third = clone(directory, 3)
    previous = result[f"{base}/run.json"]
    assert invoke() == 1
    assert "refusing to overwrite existing run keys" in capsys.readouterr().err
    assert objects(bucket)[f"{base}/run.json"] == previous
    assert set(objects(bucket)) == {
        f"prefix/runs/{run.parent.name}/{run.name}/{filename}"
        for run in [directory, third] for filename in ["events.jsonl.zst", "run.json"]
    }


def test_preview_prints_exact_keys_and_sizes_and_writes_nothing(publication, bucket, capsys):
    directory, invoke = publication
    before = digest_files(directory.parents[2])
    assert invoke("--dry-run") == 0
    output = capsys.readouterr().out
    compressed = zstandard.ZstdCompressor(level=19).compress((directory / "events.jsonl").read_bytes())
    assert f"events.jsonl.zst {len(compressed)} bytes" in output
    assert f"run.json {(directory / 'run.json').stat().st_size} bytes" in output
    base = f"s3://throwaway/prefix/runs/{directory.parent.name}/{directory.name}"
    assert output.splitlines() == [
        f"PLAN {base}/events.jsonl.zst {len(compressed)} bytes",
        f"PLAN {base}/run.json {(directory / 'run.json').stat().st_size} bytes",
    ]
    assert objects(bucket) == {} and digest_files(directory.parents[2]) == before


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("revision", [None, ""])
def test_missing_fetch_ref_is_refused_per_run(publication, bucket, capsys, preview, revision):
    directory, invoke = publication
    good = clone(directory, 2)
    def unpin(rows):
        if revision is None:
            rows[0]["event"].pop("fetch_ref")
        else:
            rows[0]["event"]["fetch_ref"] = revision
    rewrite_stream(directory, unpin)
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    before = digest_files(directory)
    assert invoke(*(["--dry-run"] if preview else [])) == 1
    output = capsys.readouterr()
    assert f"FAIL {directory.parent.name}/{directory.name}: run has no provenance.fetch_ref" in output.err
    assert good.name in output.out
    assert digest_files(directory) == before
    assert len(objects(bucket)) == (0 if preview else 2)


@pytest.mark.parametrize("option", ["--check", "--catalog"])
def test_removed_options_are_rejected_and_help_describes_dry_run(publication, bucket, capsys, option):
    _, invoke = publication
    with pytest.raises(SystemExit) as exc:
        invoke(option)
    assert exc.value.code == 2
    assert f"unrecognized arguments: {option}" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        invoke("--help")
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert option not in help_text
    assert "--dry-run verify and print exact object keys and sizes; write nothing" in help_text
    assert objects(bucket) == {}


def test_publisher_uses_only_head_and_put_without_conditional_puts(publication, bucket):
    directory, _ = publication
    publisher = Publisher("s3://throwaway/prefix")
    calls = []
    def record(model, params, **kwargs):
        calls.append(model.name)
        assert "if-none-match" not in {key.lower() for key in params["headers"]}
    publisher.s3.meta.events.register("before-call.s3", record)
    publisher.upload_run(directory)
    assert calls == ["HeadObject", "HeadObject", "PutObject", "PutObject"]


@pytest.mark.parametrize("existing", ["events.jsonl.zst", "run.json"])
def test_both_run_keys_are_headed_before_refusing_upload(publication, bucket, existing):
    directory, _ = publication
    base = f"prefix/runs/{directory.parent.name}/{directory.name}"
    bucket.put_object(Bucket="throwaway", Key=f"{base}/{existing}", Body=b"original")
    publisher = Publisher("s3://throwaway/prefix")
    calls = []
    def record(model, params, **kwargs):
        calls.append((model.name, params["Key"]))
    publisher.s3.meta.events.register("before-parameter-build.s3", record)
    with pytest.raises(PublishError, match="refusing to overwrite"):
        publisher.upload_run(directory)
    assert calls == [("HeadObject", f"{base}/events.jsonl.zst"), ("HeadObject", f"{base}/run.json")]
    assert objects(bucket) == {f"{base}/{existing}": b"original"}


@pytest.mark.parametrize("damage", ["active", "bad-card", "bad-stream"])
def test_gate_reports_failure_and_continues(publication, bucket, capsys, damage):
    directory, invoke = publication
    good = clone(directory, 2)
    if damage == "bad-stream":
        (directory / "events.jsonl").write_text("{}\n")
    else:
        card = json.loads((directory / "run.json").read_bytes())
        if damage == "active":
            card["lifecycle"]["state"] = "running"
        else:
            card["inputs"]["seed"] = 99
        (directory / "run.json").write_text(json.dumps(card))
    assert invoke() == 1
    assert f"FAIL {directory.parent.name}/{directory.name}" in capsys.readouterr().err
    keys = objects(bucket)
    assert not any(directory.name in key for key in keys)
    assert any(good.name in key for key in keys)
    assert len(keys) == 2 and all(key.startswith("prefix/runs/") for key in keys)


@pytest.mark.parametrize("stem", ["condition", "run", "all"])
def test_stem_and_experiment_selection(publication, bucket, stem):
    directory, invoke = publication
    second = clone(directory, 2)
    other = clone(directory, 3, experiment="other-exp")
    args = [directory.parent.name] if stem == "condition" else [f"{second.parent.name}/{second.name}"] if stem == "run" else []
    assert invoke(*args, "--experiment", "t") == 0
    keys = objects(bucket)
    assert not any(other.name in key for key in keys)
    selected = [second] if stem == "run" else [directory, second]
    assert set(keys) == {
        f"prefix/runs/{run.parent.name}/{run.name}/{filename}"
        for run in selected for filename in ["events.jsonl.zst", "run.json"]
    }


def test_partial_existing_run_is_not_replaced(publication, bucket, capsys):
    directory, invoke = publication
    key = f"prefix/runs/{directory.parent.name}/{directory.name}/events.jsonl.zst"
    bucket.put_object(Bucket="throwaway", Key=key, Body=b"original")
    assert invoke() == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert objects(bucket) == {key: b"original"}


@pytest.mark.parametrize("profile", [None, "throwaway"])
def test_session_and_client_receive_only_user_profile(bucket, monkeypatch, profile):
    session = boto3.Session
    seen = []
    def factory(*args, **kwargs):
        seen.append((args, kwargs))
        actual = session(*args, **kwargs)
        client = actual.client
        def make(*args, **kwargs):
            seen.append((args, kwargs))
            return client(*args, **kwargs)
        actual.client = make
        return actual
    monkeypatch.setattr(boto3, "Session", factory)
    Publisher("s3://throwaway/prefix", profile)
    assert seen == [((), {"profile_name": profile}), (("s3",), {})]


@pytest.mark.parametrize("target", ["https://host/path", "s3://", "s3://bucket/a/../b", "s3://bucket/a?token=x"])
def test_target_is_explicit_s3(target):
    with pytest.raises(ValueError):
        parse_target(target)


@pytest.mark.parametrize("error", [
    NoCredentialsError(),
    ProfileNotFound(profile="throwaway"),
    ClientError({"Error": {"Code": "AccessDenied", "Message": "Write permission missing"}}, "PutObject"),
])
def test_botocore_failures_keep_actionable_messages(tmp_path, monkeypatch, capsys, error):
    def unavailable(**kwargs):
        raise error
    monkeypatch.setattr(boto3, "Session", unavailable)
    monkeypatch.setattr(sys, "argv", ["adb-runner", "publish", "--to", "s3://throwaway/prefix",
                                     "--data-dir", str(tmp_path), "--profile", "throwaway"])
    assert cli.main() == 1
    assert str(error) in capsys.readouterr().err
