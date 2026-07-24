"""Golden-fixture parser tests (plan.md §7: golden fixtures per source).

The golden file is the reviewed, expected parser output for the saved actor
sample. Any parser change that alters output must be intentional: regenerate
with `python -m tests.regen_golden` and review the diff.
"""
import json
from pathlib import Path

from atlas.ingest.parsers import magicbricks

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
GOLDEN = Path(__file__).parent / "golden" / "magicbricks_expected.json"


def test_parser_matches_golden():
    raw_items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    parsed = [magicbricks.parse(item) for item in raw_items]
    assert parsed == expected


def test_price_parsing():
    assert magicbricks.parse_price("2.42 Cr") == 24_200_000
    assert magicbricks.parse_price("95 Lac") == 9_500_000
    assert magicbricks.parse_price("1,50,00,000") == 15_000_000
    assert magicbricks.parse_price(24202000) == 24_202_000
    assert magicbricks.parse_price(None) is None
    assert magicbricks.parse_price("Price on request") is None
    # Dotted prefixes must not eat the match (review finding: 'Rs.' → None)
    assert magicbricks.parse_price("Rs. 95 Lac") == 9_500_000
    assert magicbricks.parse_price("₹ 1.53 Cr") == 15_300_000
    # Fractional numerics must not collapse to a stored price of 0
    assert magicbricks.parse_price(0.5) is None
    assert magicbricks.parse_price(0) is None


def test_malformed_numeric_fields_do_not_kill_item():
    # '12.9.3' used to raise ValueError out of _to_float, failing the item
    parsed = magicbricks.parse(
        {"listing_id": "1", "super_area_sqft": "1.234.56", "latitude": "12.9.3"}
    )
    assert parsed is not None
    assert parsed["area_sqft"] == 1.234
    assert parsed["lat"] == 12.9


def test_rera_canonicalisation():
    # Portal prefixes (TOR/...) are stripped to the registry's PRM/KA/RERA form
    assert magicbricks.canon_rera_ids(
        "TOR/PRM/KA/RERA/1251/310/PR/250304/000047"
    ) == ["PRM/KA/RERA/1251/310/PR/250304/000047"]
    # Multi-id listings keep every id
    assert magicbricks.canon_rera_ids(
        "PRM/KA/RERA/1251/308/PR/120326/008521, PRM/KA/RERA/1251/308/PR/120326/008522"
    ) == [
        "PRM/KA/RERA/1251/308/PR/120326/008521",
        "PRM/KA/RERA/1251/308/PR/120326/008522",
    ]
    assert magicbricks.canon_rera_ids(None) == []
    assert magicbricks.canon_rera_ids("no id here") == []


def test_unusable_item_returns_none():
    assert magicbricks.parse({"title": "no id at all"}) is None
