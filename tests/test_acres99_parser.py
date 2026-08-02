"""The plot source: `fatihtahta/99acres-scraper-ppe`.

Every test here guards a trap that fails SILENTLY — each one produces a
plausible number rather than an error, so nothing but an assertion catches it.

The fixture is a real actor run (2026-08-02, Attibele + Sarjapur, land under
Rs 1Cr), trimmed of media/marketing arrays the parser never reads and with
contact names redacted. It contains no builder-project record — a
`property_type: ["land"]` search returned none — so the project-skip path is
exercised with a constructed record below, and clearly marked as such.
"""
import json
from pathlib import Path

import pytest

from atlas.ingest.parsers import SKIP, acres99
from atlas.ingest.parsers.common import normalise_coords

FIXTURE = Path(__file__).parent / "fixtures" / "99acres_land_sample.json"
GOLDEN = Path(__file__).parent / "golden" / "99acres_expected.json"

RAW = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_id(external_id: str) -> dict:
    return next(r for r in RAW if r["listing"]["property_id"] == external_id)


# --- the golden contract ----------------------------------------------------

def test_matches_golden_output():
    """Regenerate with `python -m tests.regen_golden` and REVIEW the diff."""
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = [("<SKIP: not a listing>" if acres99.parse(r) is SKIP
               else acres99.parse(r)) for r in RAW]
    assert actual == expected


def test_every_fixture_record_parses():
    assert all(isinstance(acres99.parse(r), dict) for r in RAW)


# --- trap 1: min_area_sqft is square metres ---------------------------------

def test_square_metre_field_never_becomes_the_area():
    """111.4836 is the plot in SQUARE METRES. Taken at face value every
    Rs/sqft figure is wrong by 10.76x, and land would look like a 90%
    discount against apartments — fabricated bargains at the top of the
    briefing."""
    raw = _by_id("Z92378920")
    assert raw["property"]["area"]["min_area_sqft"] == pytest.approx(111.4836)
    parsed = acres99.parse(raw)
    assert parsed["area_sqft"] == pytest.approx(1200.0)
    assert parsed["price_inr"] == 9_000_000
    # ...so Rs/sqft lands at ~7,500, not ~80,000.
    assert parsed["price_inr"] / parsed["area_sqft"] == pytest.approx(7500, rel=0.01)


def test_no_parsed_area_is_ever_a_square_metre_value():
    """Blanket guard across the whole fixture: a 1,200 sqft plot must never
    come through as ~111."""
    for raw in RAW:
        parsed = acres99.parse(raw)
        metres = raw["property"]["area"].get("min_area_sqft")
        if metres and parsed["area_sqft"]:
            assert parsed["area_sqft"] != pytest.approx(metres, rel=0.01)
            assert parsed["area_sqft"] > metres


def test_area_method_is_recorded_as_evidence():
    parsed = acres99.parse(_by_id("Z92378920"))
    assert "super_sqft" in parsed["area_method"]
    assert "agrees with" in parsed["area_method"]


def test_corroborated_area_beats_a_fudged_price_per_sqft():
    """O91299320 states 1,200 sqft three independent ways while price/ppsf
    implies 1,280 — the seller rounded the rate, not the plot. An earlier
    version preferred the arithmetic and silently invented a 7% bigger plot.
    """
    raw = _by_id("O91299320")
    pricing = raw["pricing"]
    assert pricing["min_price"] / pricing["price_sqft"] == pytest.approx(1280.0)
    assert acres99.parse(raw)["area_sqft"] == pytest.approx(1200.0)


def test_unit_sized_disagreement_still_rejects_the_area_field():
    """The price cross-check is a UNIT guard: a rounding is tolerated, a
    10.76x gap is not."""
    raw = json.loads(json.dumps(_by_id("Z92378920")))
    # Pretend every area field came through in square metres.
    raw["property"]["area"] = {"super_sqft": 111.4836, "display_area": "111.4836 sqft",
                               "min_area_sqft": 111.4836}
    area, method = acres99.resolve_area_sqft(raw)
    assert area == pytest.approx(1200.0)          # from price / price_sqft
    assert "unit-sized factor" in method


def test_area_is_none_rather_than_a_guess_when_nothing_corroborates():
    raw = {"record_type": "property_listing",
           "listing": {"property_id": "X1"},
           "property": {"area": {}}, "pricing": {}}
    area, method = acres99.resolve_area_sqft(raw)
    assert area is None
    assert method == "unresolved"
    # The listing still parses — it just carries no area, so price_per_sqft
    # (a generated column) goes null and no corrupt Rs/sqft reaches a score.
    assert acres99.parse(raw)["area_sqft"] is None


# --- trap 2: builder projects are not listings ------------------------------
# CONSTRUCTED records — the live sample contained no project card.

def test_builder_project_is_skipped_not_failed():
    project = {"record_type": "project", "entity": {"title": "Some Layout"},
               "pricing": {"min_price": 5_000_000}}
    assert acres99.parse(project) is SKIP


def test_record_without_a_property_id_is_skipped():
    """A project card has no per-unit id, so it is not addressable at all."""
    project = {"record_type": "property_listing",
               "entity": {"title": "Phase 2 launching soon"},
               "relationships": {"project": {"project_name": "Big Layout"}}}
    assert acres99.parse(project) is SKIP


def test_skip_is_falsy_but_distinguishable_from_a_parse_failure():
    assert not SKIP
    assert SKIP is not None
    assert acres99.parse("not a dict") is None


# --- trap 3: transposed coordinates -----------------------------------------

def test_transposed_coordinates_are_corrected():
    """V92378908 really came back as latitude 77.6 / longitude 13.0. A plain
    range check misses it — 77.6 is a legal latitude somewhere, just not in
    India — and the resulting geohash points into the Barents Sea."""
    raw = _by_id("V92378908")
    assert raw["location"]["coordinates"]["latitude"] == pytest.approx(77.618233)
    parsed = acres99.parse(raw)
    assert parsed["lat"] == pytest.approx(13.002091)
    assert parsed["lon"] == pytest.approx(77.618233)


def test_every_parsed_coordinate_is_inside_india():
    for raw in RAW:
        parsed = acres99.parse(raw)
        if parsed["lat"] is not None:
            assert 6.0 <= parsed["lat"] <= 38.0
            assert 68.0 <= parsed["lon"] <= 98.0


def test_implausible_coordinates_are_dropped_not_guessed():
    assert normalise_coords(0, 0) == (None, None)
    assert normalise_coords(51.5, -0.12) == (None, None)     # London
    assert normalise_coords(None, 77.6) == (None, None)


# --- ordinary mapping -------------------------------------------------------

def test_rera_ids_come_from_both_unit_and_project_fields():
    """Either can be the id that joins to the Karnataka registry, so both are
    canonicalised. Taking only the unit's would drop 8 of the fixture's 11."""
    unit_only = sum(1 for r in RAW
                    if r.get("attributes", {}).get("compliance", {})
                    .get("rera_registration_id"))
    with_both = sum(1 for r in RAW if acres99.parse(r)["rera_ids"])
    assert unit_only == 3
    assert with_both == 11
    assert all(i.startswith("PRM/KA/RERA/")
               for r in RAW for i in acres99.parse(r)["rera_ids"])


def test_lister_kind_is_normalised():
    kinds = {acres99.parse(r)["lister_kind"] for r in RAW}
    assert kinds <= {"owner", "broker", "builder"}
    assert "owner" in kinds and "broker" in kinds


def test_posted_at_is_parsed_for_real_days_on_market():
    parsed = [acres99.parse(r) for r in RAW]
    assert all(p["posted_at"] for p in parsed)
    # Real ages, not "since Atlas noticed" — the fixture spans 2025 to 2026.
    assert any(p["posted_at"].startswith("2025") for p in parsed)


def test_locality_is_the_clean_name_not_the_city_suffixed_one():
    parsed = acres99.parse(_by_id("Z92378920"))
    assert parsed["locality"] == "Sarjapur"       # not "Sarjapur, Bangalore"


def test_land_carries_no_bedroom_or_floor():
    for raw in RAW:
        parsed = acres99.parse(raw)
        assert parsed["bhk"] is None
        assert parsed["floor"] is None


def test_parsed_shape_matches_the_magicbricks_parser():
    """Both feed the same pipeline, so the required keys must line up — a
    missing key is a KeyError deep inside _apply_parsed at ingest time."""
    from atlas.ingest.parsers import magicbricks

    mb_fixture = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
    mb = magicbricks.parse(json.loads(mb_fixture.read_text(encoding="utf-8"))[0])
    ac = acres99.parse(RAW[0])
    assert set(mb) <= set(ac)
