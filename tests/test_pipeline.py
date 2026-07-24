"""End-to-end Phase-0 gate: one fixture source flows raw → parsed → stored,
re-runs are idempotent, and change tracking records price moves."""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

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
    assert listing.parser_version == "magicbricks/1.0.0"
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


def test_failed_fetch_is_recorded_not_swallowed(session):
    result = run_source(session, make_spec(Path("does/not/exist.json")))
    assert result.status == "failed"
    run = session.get(ScrapeRun, result.run_id)
    assert run.status == "failed"
    assert "fetch" in run.error
    assert run.finished_at is not None
