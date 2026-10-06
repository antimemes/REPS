"""CLI value shorthand — a frozen surface: oneliners are copy-pasted from papers."""

import pytest

from reps_runner.shorthand import ShorthandError, parse_value


def test_json_and_bare_values():
    assert parse_value('{"a": 1}') == {"a": 1}
    assert parse_value("true") is True
    assert parse_value("3") == 3
    assert parse_value("hello") == "hello"  # bare string fallback


def test_at_file(tmp_path):
    f = tmp_path / "v.json"
    f.write_text('[{"model": "m", "role": "r"}]')
    assert parse_value(f"@{f}") == [{"model": "m", "role": "r"}]


@pytest.mark.parametrize("bad", [
    "[1,2",                  # unbalanced JSON-shaped value
    '{"a": nope}',           # malformed JSON-shaped value
    "",
])
def test_errors(bad):
    with pytest.raises((ShorthandError, ValueError)):
        parse_value(bad)


def test_at_file_raw_text_fallback(tmp_path):
    # a text param's file (a prompt draft) IS the value; JSON-shaped files stay strict
    md = tmp_path / "draft.md"
    md.write_text("# A Skill\nnot json at all")
    assert parse_value(f"@{md}") == "# A Skill\nnot json at all"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ShorthandError, match="malformed JSON"):
        parse_value(f"@{bad}")
