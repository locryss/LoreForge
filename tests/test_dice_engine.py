"""Tests for cogs/dice.py against its CURRENT API.

Replaces the archived test_dice.py, which targeted a long-removed API
(parse_dice / roll_dice / DiceCog) and had been failing at collection since the
grouped-cogs refactor -- so dice had zero real coverage.
"""
import random
import pytest

from cogs.dice import parse_dice_expr, execute_roll, MAX_DICE, MAX_SIDES


# ── parse_dice_expr ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,expected", [
    ("d20",       (1, 20, None, None, 0)),
    ("1d20",      (1, 20, None, None, 0)),
    ("4d6",       (4, 6,  None, None, 0)),
    ("2d6+3",     (2, 6,  None, None, 3)),
    ("1d8-1",     (1, 8,  None, None, -1)),
    ("4d6kh3",    (4, 6,  "kh", 3, 0)),
    ("2d20kl1",   (2, 20, "kl", 1, 0)),
    ("4d6kh3+2",  (4, 6,  "kh", 3, 2)),
])
def test_parse_valid(expr, expected):
    assert parse_dice_expr(expr) == expected


def test_parse_is_case_and_space_insensitive():
    assert parse_dice_expr("  4D6KH3  ") == (4, 6, "kh", 3, 0)


@pytest.mark.parametrize("expr", [
    "", "d", "20", "abc", "d0", "d1",           # malformed / too few sides
    "0d6",                                       # zero dice
    f"{MAX_DICE + 1}d6",                         # too many dice
    f"1d{MAX_SIDES + 1}",                        # too many sides
    "2d6kh5",                                    # keep more than rolled
    "2d6kh0",                                    # keep none
    "1d20+",                                     # dangling modifier
])
def test_parse_rejects(expr):
    assert parse_dice_expr(expr) is None


def test_parse_boundaries_allowed():
    assert parse_dice_expr(f"{MAX_DICE}d{MAX_SIDES}") == (MAX_DICE, MAX_SIDES, None, None, 0)
    assert parse_dice_expr("1d2") == (1, 2, None, None, 0)


# ── execute_roll ──────────────────────────────────────────────────────────────

def test_roll_shape_and_range():
    random.seed(1)
    raw, kept, total, details = execute_roll(4, 6)
    assert len(raw) == 4 and kept == raw
    assert all(1 <= r <= 6 for r in raw)
    assert total == sum(raw)
    assert isinstance(details, str)


def test_modifier_is_applied_once():
    random.seed(2)
    raw, kept, total, _ = execute_roll(2, 6, modifier=5)
    assert total == sum(kept) + 5


def test_keep_highest_keeps_the_largest():
    random.seed(3)
    raw, kept, total, _ = execute_roll(4, 6, keep_mode="kh", keep_n=3)
    assert len(kept) == 3
    assert sorted(kept, reverse=True) == sorted(raw, reverse=True)[:3]
    assert total == sum(kept)


def test_keep_lowest_keeps_the_smallest():
    random.seed(4)
    raw, kept, total, _ = execute_roll(4, 6, keep_mode="kl", keep_n=1)
    assert len(kept) == 1
    assert kept[0] == min(raw)


def test_dropped_dice_are_struck_through():
    random.seed(5)
    raw, kept, _, details = execute_roll(4, 6, keep_mode="kh", keep_n=3)
    # exactly one die is dropped, so exactly one strikethrough pair
    assert details.count("~~") == 2


def test_negative_modifier_can_go_below_zero():
    random.seed(6)
    _, kept, total, _ = execute_roll(1, 2, modifier=-10)
    assert total == sum(kept) - 10
    assert total < 0


def test_rolls_stay_in_range_over_many_iterations():
    random.seed(7)
    for _ in range(500):
        raw, _, _, _ = execute_roll(3, 20)
        assert all(1 <= r <= 20 for r in raw)


def test_single_die_max_is_bolded_as_a_crit():
    random.seed(8)
    # d2 rolled enough times will hit the max; details bolds a natural max
    for _ in range(50):
        raw, _, _, details = execute_roll(1, 2)
        if raw[0] == 2:
            assert "**2**" in details
            break
    else:
        pytest.fail("never rolled the max on a d2 in 50 tries")


def test_parse_then_execute_roundtrip():
    parsed = parse_dice_expr("4d6kh3+2")
    assert parsed is not None
    random.seed(9)
    raw, kept, total, _ = execute_roll(*parsed)
    assert len(raw) == 4 and len(kept) == 3
    assert total == sum(kept) + 2
