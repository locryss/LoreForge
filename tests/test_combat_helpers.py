"""Tests for cogs/combat.py's pure display helpers, against the CURRENT API.

Replaces the archived test_combat_commands.py, which targeted combat_start /
combat_end / combat_join -- an API removed when combat was redesigned into the
11-subcommand HP board (open, add, add-npc, hp, condition, board, log, history,
remove, close, status). Combat had zero coverage after that.

These cover the two functions that render state to the user and take no DB or
Discord objects, so they're the parts worth pinning without a live session.
"""
import pytest

from cogs.combat import _hp_bar, _condition_tags, _CONDITION_EMOJIS


# ── _hp_bar ───────────────────────────────────────────────────────────────────

def test_full_health_is_all_filled():
    out = _hp_bar(10, 10, length=10)
    assert "█" * 10 in out
    assert "░" not in out
    assert "100%" in out


def test_zero_health_is_all_empty():
    out = _hp_bar(0, 10, length=10)
    assert "░" * 10 in out
    assert "█" not in out
    assert "0%" in out


def test_half_health():
    out = _hp_bar(5, 10, length=10)
    assert "█" * 5 in out and "░" * 5 in out
    assert "50%" in out


def test_bar_length_is_always_respected():
    for cur in range(0, 21):
        out = _hp_bar(cur, 20, length=10)
        bar = out.split("`")[1]
        assert len(bar) == 10, f"cur={cur} produced a {len(bar)}-char bar"


def test_negative_hp_clamps_to_empty_not_negative():
    # a combatant dropped below 0 must not produce a negative-width bar
    out = _hp_bar(-50, 10, length=10)
    assert "░" * 10 in out
    assert "-" not in out.split("`")[2]


def test_zero_max_hp_does_not_divide_by_zero():
    out = _hp_bar(5, 0, length=10)
    assert out == "░" * 10


def test_overhealed_does_not_overflow_the_bar():
    out = _hp_bar(30, 10, length=10)
    bar = out.split("`")[1]
    assert len(bar) == 10


@pytest.mark.parametrize("length", [1, 5, 10, 20])
def test_custom_lengths(length):
    out = _hp_bar(1, 2, length=length)
    assert len(out.split("`")[1]) == length


# ── _condition_tags ───────────────────────────────────────────────────────────

def test_no_conditions_is_empty_string():
    assert _condition_tags([]) == ""


def test_known_condition_uses_its_emoji():
    known = next(iter(_CONDITION_EMOJIS))
    out = _condition_tags([known])
    assert _CONDITION_EMOJIS[known] in out
    assert known in out


def test_unknown_condition_falls_back_to_warning():
    out = _condition_tags(["definitely-not-a-real-condition"])
    assert "⚠️" in out


def test_condition_lookup_is_case_insensitive():
    known = next(iter(_CONDITION_EMOJIS))
    assert _CONDITION_EMOJIS[known] in _condition_tags([known.upper()])


def test_multiple_conditions_all_appear():
    out = _condition_tags(["poisoned", "stunned", "prone"])
    for c in ("poisoned", "stunned", "prone"):
        assert c in out
