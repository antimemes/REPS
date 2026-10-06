"""Golden condition-identity vector; see docs/book/src/running/model.md.

Changing it requires an explicit identity-format decision.
"""

from reps_runner.canonical import abbrev, condition_id

GOLDEN_PARAMS = {
    "players": {"~zip": {"model": ["mock/a", "mock/b", "mock/c", "mock/d"],
                         "role": {"~perm": {"werewolf": 1, "villager": 2, "seer": 1}}}},
    "discussion_rounds": 2,
    "max_message_tokens": 300,
    "max_days": 10,
}
# This source is fixed test data, not a live repository reference. Keep it and
# GOLDEN_HASH unchanged across project or repository renames.
GOLDEN_SOURCE = "github:antimemetics-institute/adb/0000000000000000000000000000000000000000"
GOLDEN_HASH = "ae70d6ab71a10b0ff1b21fee0aa14d5bffbfe685"


def test_golden_vector():
    assert condition_id("werewolf", GOLDEN_SOURCE, GOLDEN_PARAMS) == GOLDEN_HASH


def test_key_order_irrelevant():
    a = {"x": 1, "y": [{"m": "a", "r": "b"}]}
    b = {"y": [{"r": "b", "m": "a"}], "x": 1}
    assert condition_id("e", "s", a) == condition_id("e", "s", b)


def test_every_identity_component_matters():
    base = condition_id("e", "s", {"x": 1})
    assert condition_id("e2", "s", {"x": 1}) != base   # experiment
    assert condition_id("e", "s2", {"x": 1}) != base   # source (per-experiment content id)
    assert condition_id("e", "s", {"x": 2}) != base    # params


def test_jcs_conflates_int_and_equal_float():
    # RFC 8785 serializes numbers ECMAScript-style: 1 and 1.0 are the same condition.
    # Documented spec behavior, not a bug.
    assert condition_id("e", "s", {"x": 1}) == condition_id("e", "s", {"x": 1.0})


def test_unicode_params_hash_stably():
    a = condition_id("e", "s", {"goal": "räv över ån — 狼"})
    assert len(a) == 40 and a == condition_id("e", "s", {"goal": "räv över ån — 狼"})


def test_abbrev():
    assert abbrev(GOLDEN_HASH) == GOLDEN_HASH[:12]
