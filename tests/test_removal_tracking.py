"""Removed/relisted lifecycle: staleness sweep with the dead-scraper guard,
and same-id relist on reappearance."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from atlas.ingest.pipeline import run_source, sweep_stale_listings
from atlas.ingest.registry import SourceSpec
from atlas.models import Listing, ListingVersion, ScrapeRun

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
N_ITEMS = 15


def make_spec(path: Path) -> SourceSpec:
    return SourceSpec(name="magicbricks", city="bangalore", kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(path)})


def _subset(tmp_path, keep_ids, name="subset.json") -> Path:
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    kept = [i for i in items if i["listing_id"] in keep_ids]
    p = tmp_path / name
    p.write_text(json.dumps(kept), encoding="utf-8")
    return p


def _age_last_seen(session, external_id, days):
    listing = session.scalar(select(Listing).where(Listing.external_id == external_id))
    listing.last_seen_at = datetime.now(timezone.utc) - timedelta(days=days)
    session.commit()


def test_sweep_marks_only_stale_listings_removed(session):
    spec = make_spec(FIXTURE)
    run_source(session, spec)
    # Age one listing past the 7-day cutoff
    _age_last_seen(session, "84945537", days=10)

    removed = sweep_stale_listings(session, spec, stale_days=7)
    assert removed == 1

    gone = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    assert gone.status == "removed"
    assert gone.removed_at is not None
    kinds = session.scalars(
        select(ListingVersion.change_kind)
        .where(ListingVersion.listing_id == gone.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert kinds == ["new", "removed"]
    # Everyone else untouched
    assert session.scalar(
        select(func.count(Listing.id)).where(Listing.status == "active")
    ) == N_ITEMS - 1


def test_sweep_skips_when_no_recent_healthy_run(session):
    """A dead scraper must not convert its silence into fake removals."""
    spec = make_spec(FIXTURE)
    run_source(session, spec)
    for ext in ("84945537", "85452079"):
        _age_last_seen(session, ext, days=10)

    # Backdate the only healthy run beyond the cutoff → sweep must no-op
    run = session.scalar(select(ScrapeRun))
    run.finished_at = datetime.now(timezone.utc) - timedelta(days=9)
    session.commit()

    removed = sweep_stale_listings(session, spec, stale_days=7)
    assert removed == 0
    assert session.scalar(
        select(func.count(Listing.id)).where(Listing.status == "removed")
    ) == 0


def test_relist_on_reappearance(session, tmp_path):
    spec_full = make_spec(FIXTURE)
    run_source(session, spec_full)

    # A run without 84945537, then age + sweep it to 'removed'
    subset = _subset(tmp_path, keep_ids={"85452079"}, name="without.json")
    # (don't actually ingest subset as the live source — just simulate removal)
    _age_last_seen(session, "84945537", days=10)
    assert sweep_stale_listings(session, spec_full, stale_days=7) == 1

    listing = session.scalar(select(Listing).where(Listing.external_id == "84945537"))
    assert listing.status == "removed"

    # It comes back in a later full run → relisted, removed_at cleared
    result = run_source(session, spec_full)
    assert result.relisted == 1
    session.refresh(listing)
    assert listing.status == "relisted"
    assert listing.removed_at is None
    kinds = session.scalars(
        select(ListingVersion.change_kind)
        .where(ListingVersion.listing_id == listing.id)
        .order_by(ListingVersion.observed_at)
    ).all()
    assert kinds == ["new", "removed", "relisted"]
