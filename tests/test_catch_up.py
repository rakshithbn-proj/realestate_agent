"""Startup catch-up after downtime.

APScheduler's in-memory jobstore does not replay a fire that happened while the
process was down — on boot the next run time is computed from now. So a
container restart spanning the morning window would silently cost a gate day
(and the Phase-1 streak) unless startup notices and collects.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from atlas import jobs
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import ScrapeRun, Source

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
IST = ZoneInfo("Asia/Kolkata")


def _spec(city: str = "bangalore") -> SourceSpec:
    return SourceSpec(name="magicbricks", city=city, kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(FIXTURE)})


@pytest.fixture()
def wired(session, engine, monkeypatch):
    """Point jobs.* at the test database and stub the sources it would run."""
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "_session", lambda: factory())
    monkeypatch.setattr(jobs, "SOURCES", {"magicbricks": _spec()})
    # RERA does a live HTTP fetch; the catch-up decision is what's under test.
    monkeypatch.setattr(jobs, "ingest_rera", lambda: None)
    monkeypatch.setattr(jobs, "SCHEDULE",
                        (("portals_daily", jobs.ingest_portals, 6, 0),))
    return session


def _freeze(monkeypatch, hour: int, minute: int = 0):
    """Pretend 'now' in IST is today at hour:minute."""
    real = datetime.now(IST)
    fake = real.replace(hour=hour, minute=minute, second=0, microsecond=0)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake if tz is not None else real
    monkeypatch.setattr(jobs, "datetime", _DT)


def _runs(session) -> int:
    return len(session.scalars(select(ScrapeRun)).all())


def test_catches_up_when_the_window_passed_and_nothing_ran(wired, monkeypatch):
    _freeze(monkeypatch, 9)          # booted at 09:00, well after 06:00
    assert jobs.catch_up_if_missed() is True
    assert _runs(wired) == 1         # the missed day was collected


def test_no_catch_up_before_the_window_opens(wired, monkeypatch):
    """Booting at 05:00 must not pre-empt the scheduler's own 06:00 fire."""
    _freeze(monkeypatch, 5)
    assert jobs.catch_up_if_missed() is False
    assert _runs(wired) == 0


def test_no_catch_up_when_today_is_already_clean(wired, monkeypatch):
    """The guard that stops a restart loop from re-collecting all day."""
    run_source(wired, _spec())
    before = _runs(wired)
    _freeze(monkeypatch, 9)
    assert jobs.catch_up_if_missed() is False
    assert _runs(wired) == before


def test_catch_up_runs_again_while_today_is_still_dirty(wired, monkeypatch):
    """A failed source today means the day is not saved yet — a later restart
    should retry, since a same-day success rescues the day for the gate."""
    run_source(wired, _spec())
    src = wired.scalar(select(Source).where(Source.name == "magicbricks"))
    wired.execute(ScrapeRun.__table__.update()
                  .where(ScrapeRun.source_id == src.id)
                  .values(status="failed"))
    wired.commit()

    _freeze(monkeypatch, 9)
    assert jobs.catch_up_if_missed() is True
    assert _runs(wired) == 2


def test_yesterdays_dirt_does_not_trigger_a_catch_up(wired, monkeypatch):
    """Catch-up is about TODAY. It must not try to re-collect the past —
    those days are gone and re-running cannot change them."""
    run_source(wired, _spec())
    src = wired.scalar(select(Source).where(Source.name == "magicbricks"))
    yesterday = (datetime.now(IST) - timedelta(days=1)).replace(hour=6)
    wired.execute(ScrapeRun.__table__.update()
                  .where(ScrapeRun.source_id == src.id)
                  .values(started_at=yesterday, finished_at=yesterday,
                          status="failed"))
    wired.commit()
    # Today now has no runs at all, and the window has passed -> catch up once.
    _freeze(monkeypatch, 9)
    assert jobs.catch_up_if_missed() is True
    # ...and immediately after, today is clean, so a second restart is a no-op.
    assert jobs.catch_up_if_missed() is False


def test_schedule_and_scheduler_agree(monkeypatch):
    """The catch-up reads SCHEDULE; the scheduler must be built from the same
    data, or the window it waits for and the window it collects for drift."""
    # build_scheduler() only constructs; it never starts, so there is nothing
    # to shut down here.
    scheduler = jobs.build_scheduler()
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == {job_id for job_id, _, _, _ in jobs.SCHEDULE}
    # One scheduled job per SCHEDULE entry: a duplicate id would silently
    # collapse two jobs into one and the set comparison above would still pass.
    assert len(ids) == len(jobs.SCHEDULE) > 0


def test_catch_up_failure_is_contained_not_swallowed(monkeypatch, caplog):
    """A crash in the thread must not kill app startup, but must be logged."""
    monkeypatch.setattr(jobs, "catch_up_if_missed",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    jobs._catch_up_safely()          # must not raise
    assert "startup catch-up failed" in caplog.text
