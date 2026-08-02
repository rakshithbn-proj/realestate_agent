"""End-to-end Phase-0 gate: one fixture source flows raw → parsed → stored,
re-runs are idempotent, and change tracking records price moves."""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from atlas.ingest.parsers import magicbricks
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import (
    Listing,
    ListingVersion,
    Locality,
    PriceEvent,
    RawPayload,
    ScrapeRun,
)

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
N_ITEMS = 15


def make_spec(path: Path) -> SourceSpec:
    return SourceSpec(
        name="magicbricks", city="bangalore", kind="portal",
        fetcher="fixture", parser="magicbricks", params={"path": str(path)},
    )


def test_fixture_flows_raw_parsed_stored(session):
    result = run_source(session, make_spec(FIXTURE))

    assert result.status == "ok"
    assert result.items_found == N_ITEMS
    assert result.new == N_ITEMS
    assert result.failed == 0

    # Raw first: every item archived before parsing
    assert session.scalar(select(func.count(RawPayload.id))) == N_ITEMS
    # Parsed and stored
    assert session.scalar(select(func.count(Listing.id))) == N_ITEMS
    # Every new listing got a 'new' version snapshot
    kinds = session.scalars(select(ListingVersion.change_kind)).all()
    assert kinds == ["new"] * N_ITEMS

    session.expire_all()
    listing = session.scalar(select(Listing).where(Listing.external_id == "85234497"))
    assert listing is not None
    # TOR/-prefixed RERA id canonicalised to the registry form (handoff §7)
    assert listing.rera_ids == ["PRM/KA/RERA/1251/310/PR/250304/000047"]
    # Stamped with whatever version parsed it (plan §7) — asserted against the
    # module constant, not a literal, so a deliberate bump doesn't read as a
    # regression here. The golden file is what guards the mapping itself.
    assert listing.parser_version == magicbricks.PARSER_VERSION
    assert listing.city == "bangalore"
    assert listing.lister_kind == "broker"          # 'agent' mapped
    assert listing.price_inr == 15_390_000
    # Generated column computed by Postgres
    assert float(listing.price_per_sqft) == pytest.approx(15_390_000 / 1311, rel=1e-6)
    assert listing.geohash6 is not None and len(listing.geohash6) == 6

    # Locality resolved, city-scoped (multi-city schema, handoff §4a)
    loc = session.get(Locality, listing.locality_id)
    assert (loc.name, loc.city) == ("Hosa Road", "bangalore")
    # 7 distinct localities in the fixture; one item has none
    assert session.scalar(select(func.count(Locality.id))) == 7
    no_locality = session.scalar(select(Listing).where(Listing.external_id == "85416183"))
    assert no_locality.locality_id is None

    # Raw payload is the untouched actor item
    raw = session.scalar(select(RawPayload).where(RawPayload.external_id == "85234497"))
    assert raw.payload["price_display"] == "1.53 Cr"

    run = session.get(ScrapeRun, result.run_id)
    assert run.status == "ok"
    assert run.finished_at is not None


def test_rerun_is_idempotent(session):
    run_source(session, make_spec(FIXTURE))
    result2 = run_source(session, make_spec(FIXTURE))

    assert result2.status == "ok"
    assert result2.new == 0
    assert result2.unchanged == N_ITEMS
    assert session.scalar(select(func.count(Listing.id))) == N_ITEMS
    # No spurious versions on an unchanged re-observation
    assert session.scalar(select(func.count(ListingVersion.id))) == N_ITEMS
    # But raw payloads are archived for every run
    assert session.scalar(select(func.count(RawPayload.id))) == 2 * N_ITEMS


def test_price_change_and_update_tracked(session, tmp_path):
    run_source(session, make_spec(FIXTURE))

    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items[0]["price_inr"] = 22_000_000          # was 24_202_000 → price drop
    items[1]["title"] = "3BHK — RELISTED with new title"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(changed))
    assert result.price_changed == 1
    assert result.updated == 1
    assert result.unchanged == N_ITEMS - 2

    listing = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    session.refresh(listing)
    assert listing.price_inr == 22_000_000

    events = session.scalars(
        select(PriceEvent)
        .where(PriceEvent.listing_id == listing.id)
        .order_by(PriceEvent.observed_at)
    ).all()
    assert [e.new_price for e in events] == [24_202_000, 22_000_000]
    assert float(events[-1].pct_change) == pytest.approx(-9.1, abs=0.05)

    version_kinds = session.scalars(
        select(ListingVersion.change_kind)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert version_kinds == ["new", "price_changed"]


def test_vanished_price_is_preserved_and_recorded(session, tmp_path):
    run_source(session, make_spec(FIXTURE))

    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del items[0]["price_inr"]           # portal now says "price on request"
    del items[0]["price_display"]
    changed = tmp_path / "no_price.json"
    changed.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(changed))
    assert result.updated == 1          # disappearance is a recorded change...

    listing = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    session.refresh(listing)
    assert listing.price_inr == 24_202_000   # ...but the price is not wiped

    versions = session.scalars(
        select(ListingVersion)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert [v.change_kind for v in versions] == ["new", "updated"]
    assert versions[-1].snapshot["price_inr"] is None   # evidence of the gap
    # No bogus PriceEvent for a missing price
    assert session.scalar(
        select(func.count(PriceEvent.id)).where(PriceEvent.listing_id == listing.id)
    ) == 1


def test_locality_change_is_recorded(session, tmp_path):
    run_source(session, make_spec(FIXTURE))

    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items[0]["locality"] = "Hebbal"     # was Yelahanka
    changed = tmp_path / "moved.json"
    changed.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(changed))
    assert result.updated == 1

    listing = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    session.refresh(listing)
    loc = session.get(Locality, listing.locality_id)
    assert (loc.name, loc.city) == ("Hebbal", "bangalore")
    kinds = session.scalars(
        select(ListingVersion.change_kind)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert kinds == ["new", "updated"]


def test_portal_city_spelling_does_not_fork_market(session, tmp_path):
    run_source(session, make_spec(FIXTURE))

    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for item in items:
        item["city"] = "Bengaluru"      # portal respelling, same market
    changed = tmp_path / "respelled.json"
    changed.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(changed))
    assert result.unchanged == N_ITEMS  # spelling is not a market change

    cities = set(session.scalars(select(Listing.city)).all())
    assert cities == {"bangalore"}      # registry slug stays authoritative
    assert set(session.scalars(select(Locality.city)).all()) == {"bangalore"}


def test_low_item_failure_stays_ok_but_is_noted(session, tmp_path):
    # One bad item out of 15 (< ANOMALY_UNPARSED_RATIO) must keep the run 'ok'
    # so the source stays authorized to sweep — a flaky scraper dropping one
    # item a day must not silently freeze removal tracking.
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items[0]["bhk"] = 99_999            # overflows smallint at the DB
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(bad))

    assert result.status == "ok"        # tolerated, not anomalous
    assert result.failed == 1
    assert result.new == N_ITEMS - 1
    assert session.scalar(select(func.count(RawPayload.id))) == N_ITEMS
    run = session.get(ScrapeRun, result.run_id)
    assert run.finished_at is not None
    # The failure is still surfaced in the run row, citing the item
    assert "within tolerance" in run.error
    assert "84945537" in run.error


def test_mostly_stub_run_is_anomalous(session, tmp_path):
    # >30% unparsed = the acres99 silent-collapse mode → anomalous
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for item in items[:8]:              # 8/15 = 53% have no usable id
        item.pop("listing_id", None)
    bad = tmp_path / "stubs.json"
    bad.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(bad))
    assert result.status == "anomalous"
    assert result.failed == 8
    assert "unparsed ratio" in session.get(ScrapeRun, result.run_id).error


def test_empty_fetch_is_anomalous_not_ok(session, tmp_path):
    # A portal returning zero items is a block, not an empty market
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    result = run_source(session, make_spec(empty))
    assert result.status == "anomalous"
    assert result.items_found == 0
    assert "empty fetch" in session.get(ScrapeRun, result.run_id).error


def test_relist_with_price_change_records_both_signals(session, tmp_path):
    from datetime import datetime, timedelta, timezone

    from atlas.ingest.pipeline import sweep_stale_listings
    spec = make_spec(FIXTURE)
    run_source(session, spec)

    # Age + sweep one listing to 'removed'
    listing = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    listing.last_seen_at = datetime.now(timezone.utc) - timedelta(days=10)
    session.commit()
    assert sweep_stale_listings(session, spec, stale_days=7) == 1

    # It reappears at a lower price
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items[0]["price_inr"] = 20_000_000   # was 24_202_000
    changed = tmp_path / "relist_cheaper.json"
    changed.write_text(json.dumps(items), encoding="utf-8")

    result = run_source(session, make_spec(changed))
    assert result.relisted == 1

    session.refresh(listing)
    assert listing.status == "relisted"
    assert listing.price_inr == 20_000_000
    # Both signals present: a PriceEvent AND a price_changed version, so a
    # consumer reading either reconstructs the move.
    assert session.scalar(
        select(func.count(PriceEvent.id))
        .where(PriceEvent.listing_id == listing.id, PriceEvent.new_price == 20_000_000)
    ) == 1
    kinds = session.scalars(
        select(ListingVersion.change_kind)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert kinds == ["new", "removed", "relisted", "price_changed"]


def test_failed_fetch_is_recorded_not_swallowed(session):
    result = run_source(session, make_spec(Path("does/not/exist.json")))
    assert result.status == "failed"
    run = session.get(ScrapeRun, result.run_id)
    assert run.status == "failed"
    assert "fetch" in run.error
    assert run.finished_at is not None
