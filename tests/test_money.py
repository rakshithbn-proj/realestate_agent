"""Indian number formatting.

Every rupee figure in Atlas is read by someone who thinks in lakhs and crores.
`950,000` forces a re-parse; `9,50,000` reads as "nine and a half lakh" at a
glance. On a briefing built for a fast morning scan that is not cosmetic — a
misread ceiling is a misread decision.
"""
import pytest

from atlas.money import compact, inr, rs


@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    (100, "100"),
    (999, "999"),
    (1_000, "1,000"),
    (99_999, "99,999"),
    (1_00_000, "1,00,000"),           # one lakh: the first two-digit group
    (3_50_000, "3,50,000"),           # the real deployable figure
    (9_50_000, "9,50,000"),
    (15_50_000, "15,50,000"),
    (95_49_795, "95,49,795"),
    (1_00_00_000, "1,00,00,000"),     # one crore
    (2_70_00_000, "2,70,00,000"),
    (10_52_31_500, "10,52,31,500"),
])
def test_indian_grouping(value, expected):
    assert inr(value) == expected


def test_never_uses_western_grouping():
    """The specific regression: three-digit groups all the way up."""
    for value in (9_50_000, 95_49_795, 2_70_00_000):
        assert inr(value) != f"{value:,}"


def test_negatives_and_none():
    assert inr(-50_000) == "-50,000"
    assert inr(-95_49_795) == "-95,49,795"
    assert inr(None) == "-"
    assert inr(None, dash="on request") == "on request"


def test_rounds_rather_than_truncating():
    assert inr(9_28_529.6) == "9,28,530"


def test_rs_adds_the_prefix():
    assert rs(3_50_000) == "Rs 3,50,000"
    assert rs(None) == "-"


@pytest.mark.parametrize("value,expected", [
    (3_50_000, "3.5L"),
    (95_49_795, "95.5L"),
    (2_70_00_000, "2.7Cr"),
    (1_00_00_000, "1Cr"),
    (50_000, "50,000"),               # below a lakh, stay exact
])
def test_compact_form(value, expected):
    assert compact(value) == expected
