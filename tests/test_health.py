"""Source health: 'no listings' vs 'scraper dead' must be distinguishable."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from atlas.health import source_health
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import ScrapeRun, Source

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"


def make_spec(path: Path) -> SourceSpec:
    return SourceSpec(name="magicbricks", city="bangalore", kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(path)})


def _health_for(session, name):
    return next(h for h in source_health(session) if h.name == name)


def test_healthy_after_ok_run(session):
    run_source(session, make_spec(FIXTURE))
    h = _health_for(session, "magicbricks")
    assert h.healthy is True
    assert h.last_run_status == "ok"
    assert h.last_items_found == 15
    assert h.consecutive_bad_runs == 0


def test_consecutive_failures_flip_unhealthy(session):
    spec = make_spec(FIXTURE)
    run_source(session, spec)  # ok, establishes the source
    src = session.scalar(select(Source).where(Source.name == "magicbricks"))
    now = datetime.now(timezone.utc)
    # Two failures AFTER the ok run (most-recent-first ordering matters)
    for i in range(2):
        session.add(ScrapeRun(source_id=src.id, status="failed",
                              started_at=now + timedelta(minutes=i + 1),
                              finished_at=now + timedelta(minutes=i + 1),
                              items_found=0, error="boom"))
    session.commit()

    h = _health_for(session, "magicbricks")
    assert h.healthy is False
    assert h.consecutive_bad_runs == 2
    assert "consecutive" in h.reason
    # But the last OK run is still remembered
    assert h.last_ok_at is not None


def test_silent_source_flagged(session):
    spec = make_spec(FIXTURE)
    run_source(session, spec)
    src = session.scalar(select(Source).where(Source.name == "magicbricks"))
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    run = session.scalar(select(ScrapeRun).where(ScrapeRun.source_id == src.id))
    run.started_at = old
    run.finished_at = old
    session.commit()

    h = _health_for(session, "magicbricks")
    assert h.healthy is False
    assert "silent" in h.reason
