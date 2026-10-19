"""Real verification, boto3 profile resolution and S3 semantics, with no live service."""

import json
import shutil
import sys

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
from moto import mock_aws
import pytest
import zstandard

from reps_events import read_events
from reps_runner import cli
from reps_runner.card import derive_card
from reps_runner.publish import Publisher, PublishError, parse_target
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
    # Publishable evidence records a pinned revision in the stream and the card.
    rewrite_stream(directory, lambda rows: rows[0]["event"].update(fetch_ref="github:owner/repo/" + "a" * 40))
    (directory / "run.json").write_text(json.dumps(derive_card(read_events(directory))))
    home = tmp_path / "data dir's"
    directory.parents[2].rename(home)
    directory = home / "runs" / directory.parent.name / directory.name
    monkeypatch.setenv("REPS_MANIFEST", str(manifest))
    monkeypatch.setenv("REPS_DATA_DIR", str(tmp_path / "wrong-store"))
    def invoke(*args, license="CC-BY-4.0"):
        monkeypatch.setattr(sys, "argv", ["reps-runner", "publish", "--to", "s3://throwaway/prefix",
            "--data-dir", str(home), *(["--license", license] if license is not None else []), *args])
        return cli.main()
    return directory, invoke


LICENSE = b'{"v":0,"spdx":"CC-BY-4.0"}\n'


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
    assert objects(bucket) == {"prefix/license.json": LICENSE}
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
    assert decoder.eof and not decoder.unused_data  # one frame only
    assert result[f"{base}/run.json"] == (directory / "run.json").read_bytes()
    headers = bucket.head_object(Bucket="throwaway", Key=f"{base}/events.jsonl.zst")
    assert headers["ContentType"] == "application/zstd" and "ContentEncoding" not in headers
    assert not any("workspace/" in key or "conditions/" in key for key in result)
    assert set(result) == {"prefix/license.json", f"{base}/events.jsonl.zst", f"{base}/run.json"}
    assert digest_files(directory.parents[2]) == before
    # Overwrite refusal applies to each run: a later run uploads.
    third = clone(directory, 3)
    previous = result[f"{base}/run.json"]
    assert invoke() == 1
    assert "refusing to overwrite existing run keys" in capsys.readouterr().err
    assert objects(bucket)[f"{base}/run.json"] == previous
    assert set(objects(bucket)) == {"prefix/license.json"} | {
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
        f"PLAN s3://throwaway/prefix/license.json {len(LICENSE)} bytes",
        "LICENSE s3://throwaway/prefix CC-BY-4.0",
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
    assert len(objects(bucket)) == (0 if preview else 3)


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
    assert keys.pop("prefix/license.json") == LICENSE
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
    assert set(keys) == {"prefix/license.json"} | {
        f"prefix/runs/{run.parent.name}/{run.name}/{filename}"
        for run in selected for filename in ["events.jsonl.zst", "run.json"]
    }


def test_partial_existing_run_is_not_replaced(publication, bucket, capsys):
    directory, invoke = publication
    key = f"prefix/runs/{directory.parent.name}/{directory.name}/events.jsonl.zst"
    bucket.put_object(Bucket="throwaway", Key=key, Body=b"original")
    assert invoke() == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert objects(bucket) == {"prefix/license.json": LICENSE, key: b"original"}


def test_first_publish_declares_license_and_later_publishes_reuse_it(publication, bucket, monkeypatch, capsys):
    directory, invoke = publication
    assert invoke() == 0
    assert objects(bucket)["prefix/license.json"] == LICENSE
    assert bucket.head_object(Bucket="throwaway", Key="prefix/license.json")["ContentType"] == "application/json"
    assert capsys.readouterr().out.splitlines()[:2] == [
        f"PUT s3://throwaway/prefix/license.json {len(LICENSE)} bytes",
        "LICENSE s3://throwaway/prefix CC-BY-4.0",
    ]
    second = clone(directory, 2)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a declared license is never prompted for"))
    for args in [["--dry-run"], ["--license", "CC-BY-4.0", "--dry-run"], []]:
        assert invoke(*args, f"{second.parent.name}/{second.name}", license=None) == 0
        output = capsys.readouterr().out.splitlines()
        assert output[0] == "LICENSE s3://throwaway/prefix CC-BY-4.0"
        assert not any("license.json" in line for line in output)
    keys = objects(bucket)
    assert keys["prefix/license.json"] == LICENSE
    assert f"prefix/runs/{second.parent.name}/{second.name}/run.json" in keys


@pytest.mark.parametrize("preview", [False, True])
def test_mismatching_license_fails_before_any_run_upload(publication, bucket, capsys, preview):
    _, invoke = publication
    declared = b'{"v":0,"spdx":"CC0-1.0"}\n'
    bucket.put_object(Bucket="throwaway", Key="prefix/license.json", Body=declared)
    assert invoke(*(["--dry-run"] if preview else [])) == 1
    output = capsys.readouterr()
    assert output.err == ("publish: FAIL: s3://throwaway/prefix/license.json declares CC0-1.0, not CC-BY-4.0; "
                          "pass --license CC0-1.0 or choose a new prefix\n")
    assert output.out == ""
    assert objects(bucket) == {"prefix/license.json": declared}


@pytest.mark.parametrize("preview", [False, True])
def test_undeclared_target_without_license_fails_off_a_terminal(publication, bucket, capsys, preview):
    _, invoke = publication
    assert invoke(*(["--dry-run"] if preview else []), license=None) == 1
    output = capsys.readouterr()
    assert output.err == "publish: FAIL: target declares no license; pass --license SPDX\n"
    assert output.out == ""
    assert objects(bucket) == {}


@pytest.mark.parametrize("body", [
    b"{", b"\xff", b"[]", b'{"v":0}', b'{"spdx":"MIT"}', b'{"v":1,"spdx":"MIT"}', b'{"v":false,"spdx":"MIT"}',
    b'{"v":0,"spdx":"MIT License"}', b'{"v":0,"spdx":""}', b'{"v":0,"spdx":"MIT","extra":1}',
])
@pytest.mark.parametrize("license", [None, "MIT"])
def test_malformed_license_declaration_fails_without_replacing_it(publication, bucket, capsys, body, license):
    _, invoke = publication
    bucket.put_object(Bucket="throwaway", Key="prefix/license.json", Body=body)
    assert invoke(license=license) == 1
    output = capsys.readouterr()
    assert output.err.startswith("publish: FAIL: s3://throwaway/prefix/license.json: malformed license declaration")
    assert output.out == ""
    assert objects(bucket) == {"prefix/license.json": body}


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("target, key, where", [
    ("s3://throwaway/prefix", "prefix/license.json", "s3://throwaway/prefix"),
    ("s3://throwaway", "license.json", "s3://throwaway/"),
])
def test_license_is_declared_for_an_existing_prefix_without_local_runs(bucket, tmp_path, monkeypatch, capsys,
                                                                       preview, target, key, where):
    existing = f"{key.removesuffix('license.json')}runs/c-e/r/run.json"
    bucket.put_object(Bucket="throwaway", Key=existing, Body=b"existing")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sys, "argv", ["reps-runner", "publish", "--to", target, "--profile", "throwaway",
                                      "--license", "CC-BY-4.0", "--data-dir", str(empty),
                                      *(["--dry-run"] if preview else [])])
    assert cli.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{'PLAN' if preview else 'PUT'} s3://throwaway/{key} {len(LICENSE)} bytes",
        f"LICENSE {where} CC-BY-4.0",
    ]
    assert objects(bucket) == {existing: b"existing", **({} if preview else {key: LICENSE})}
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize("answers, expected", [
    ([""], "CC-BY-4.0"),
    (["3"], "CC-BY-SA-4.0"),
    (["5"], "CC-BY-NC-ND-4.0"),
    (["MIT"], "MIT"),
    (["6"], "NONE"),
    (["none"], "NONE"),
    (["7", "MIT"], "MIT"),
    (["9", "7", "MIT License", "7", "3", "NOASSERTION", "7", "", "LicenseRef-lab"], "LicenseRef-lab"),
])
def test_terminal_prompt_offers_licenses_on_stderr(publication, bucket, monkeypatch, capsys, answers, expected):
    directory, invoke = publication
    remaining = iter(answers)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: next(remaining))
    assert invoke(license=None) == 0
    assert next(remaining, None) is None
    output = capsys.readouterr()
    assert "s3://throwaway/prefix declares no license" in output.err
    for line in ["1) CC-BY-4.0 (default)", "2) CC0-1.0", "3) CC-BY-SA-4.0", "4) CC-BY-NC-SA-4.0",
                 "5) CC-BY-NC-ND-4.0", "6) No license",
                 "7) Other: an identifier from https://spdx.org/licenses/<id>.html", "license [1-7 or SPDX id]: "]:
        assert line in output.err
    assert ("SPDX identifier (https://spdx.org/licenses/<id>.html): " in output.err) is ("7" in answers)
    assert "(default)" not in output.out
    keys = objects(bucket)
    assert f"prefix/runs/{directory.parent.name}/{directory.name}/run.json" in keys
    if expected == "NONE":
        assert "LICENSE s3://throwaway/prefix undeclared" in output.out.splitlines()
        assert "prefix/license.json" not in keys
    else:
        assert f"LICENSE s3://throwaway/prefix {expected}" in output.out.splitlines()
        assert keys["prefix/license.json"] == f'{{"v":0,"spdx":"{expected}"}}\n'.encode()


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("license", ["NONE", "none"])
def test_no_license_writes_no_declaration(publication, bucket, capsys, preview, license):
    directory, invoke = publication
    assert invoke(*(["--dry-run"] if preview else []), license=license) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.splitlines()[0] == "LICENSE s3://throwaway/prefix undeclared"
    assert not any("license.json" in line for line in output.out.splitlines())
    base = f"prefix/runs/{directory.parent.name}/{directory.name}"
    assert set(objects(bucket)) == (set() if preview else {f"{base}/events.jsonl.zst", f"{base}/run.json"})


@pytest.mark.parametrize("preview", [False, True])
def test_no_license_publishes_under_an_existing_declaration_with_a_warning(publication, bucket, capsys, preview):
    directory, invoke = publication
    bucket.put_object(Bucket="throwaway", Key="prefix/license.json", Body=LICENSE)
    assert invoke(*(["--dry-run"] if preview else []), license="NONE") == 0
    output = capsys.readouterr()
    assert output.err == ("publish: WARNING: s3://throwaway/prefix/license.json declares CC-BY-4.0; "
                          "publishing under it\n")
    assert output.out.splitlines()[0] == "LICENSE s3://throwaway/prefix CC-BY-4.0"
    keys = objects(bucket)
    assert keys.pop("prefix/license.json") == LICENSE
    base = f"prefix/runs/{directory.parent.name}/{directory.name}"
    assert set(keys) == (set() if preview else {f"{base}/events.jsonl.zst", f"{base}/run.json"})


@pytest.mark.parametrize("spdx", ["NONE", "NOASSERTION", "noassertion"])
@pytest.mark.parametrize("license", [None, "NONE", "CC-BY-4.0"])
def test_handwritten_non_license_declaration_is_refused(publication, bucket, capsys, spdx, license):
    _, invoke = publication
    body = f'{{"v":0,"spdx":"{spdx}"}}\n'.encode()
    bucket.put_object(Bucket="throwaway", Key="prefix/license.json", Body=body)
    assert invoke(license=license) == 1
    assert capsys.readouterr().err == (f"publish: FAIL: s3://throwaway/prefix/license.json: "
                                       f"{spdx} is not a license; license.json must name one\n")
    assert objects(bucket) == {"prefix/license.json": body}


def test_license_declaration_gets_then_puts_once(bucket):
    publisher = Publisher("s3://throwaway/prefix")
    calls = []
    def record(model, params, **kwargs):
        calls.append((model.name, params["Key"]))
    publisher.s3.meta.events.register("before-parameter-build.s3", record)
    assert publisher.declare_license("CC-BY-4.0", log=lambda _: None) == "CC-BY-4.0"
    assert publisher.declare_license(None, log=lambda _: None) == "CC-BY-4.0"
    assert calls == [("GetObject", "prefix/license.json"), ("PutObject", "prefix/license.json"),
                     ("GetObject", "prefix/license.json")]


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
    monkeypatch.setattr(sys, "argv", ["reps-runner", "publish", "--to", "s3://throwaway/prefix",
                                     "--data-dir", str(tmp_path), "--profile", "throwaway"])
    assert cli.main() == 1
    assert str(error) in capsys.readouterr().err
