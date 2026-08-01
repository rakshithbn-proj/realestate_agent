"""Phase-1 runtime gate: consecutive clean ingestion days.

The gate is the only thing standing between Phase 1 and Phase 2, so its
counting rules need to be pinned down — an over-generous gate would declare
Phase 1 done on a dead scraper, and an over-strict one would never let the
streak start.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from atlas.gate import gate_status
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import ScrapeRun, Source

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"
IST = ZoneInfo("Asia/Kolkata")


def make_spec(city: str = "bangalore") -> SourceSpec:
    return SourceSpec(name="magicbricks", city=city, kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(FIXTURE)})


def _at(days_ago: int, hour: int = 6) -> datetime:
    """A timestamp `days_ago` IST days back, at 06:00 IST (job time)."""
    day = (datetime.now(IST) - timedelta(days=days_ago)).date()
    return datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(hour=hour)


def _source(session, city: str = "bangalore") -> Source:
    return session.scalar(
        select(Source).where(Source.name == "magicbricks", Source.city == city))


def _add_run(session, source_id: int, days_ago: int, status: str, hour: int = 6):
    ts = _at(days_ago, hour)
    session.add(ScrapeRun(source_id=source_id, status=status, started_at=ts,
                          finished_at=ts, items_found=15 if status == "ok" else 0))
    session.commit()


def _day(status, iso: str):
    return next(d for d in status.days if d.day == iso)


def test_single_ok_run_today_starts_the_streak(session):
    run_source(session, make_spec())
    status = gate_status(session)
    assert status.streak == 1
    assert status.met is False


def test_seven_clean_days_meets_the_gate(session):
    run_source(session, make_spec())          # creates the source (today)
    src = _source(session)
    for days_ago in range(1, 7):
        _add_run(session, src.id, days_ago, "ok")

    status = gate_status(session)
    assert status.streak == 7
    assert status.met is True


def test_a_failed_day_breaks_the_streak(session):
    run_source(session, make_spec())
    src = _source(session)
    for days_ago in (1, 2):
        _add_run(session, src.id, days_ago, "ok")
    _add_run(session, src.id, 3, "failed")
    for days_ago in (4, 5, 6):
        _add_run(session, src.id, days_ago, "ok")

    status = gate_status(session)
    assert status.streak == 3          # today + 2, stopped by the failure
    assert status.met is False


def test_missing_day_breaks_the_streak(session):
    """Silence is not cleanliness — a day with no run at all is dirty."""
    run_source(session, make_spec())
    src = _source(session)
    _add_run(session, src.id, 1, "ok")
    # nothing on day 2
    _add_run(session, src.id, 3, "ok")

    status = gate_status(session)
    assert status.streak == 2
    assert _day(status, _at(2).date().isoformat()).clean is False


def test_retry_later_the_same_day_rescues_the_day(session):
    """A source that fails at 06:00 and succeeds at 07:00 still ingested."""
    run_source(session, make_spec())
    src = _source(session)
    _add_run(session, src.id, 1, "failed", hour=6)
    _add_run(session, src.id, 1, "ok", hour=7)

    status = gate_status(session)
    assert status.streak == 2
    assert _day(status, _at(1).date().isoformat()).sources[
        "magicbricks/bangalore"] == "ok"


def test_anomalous_run_is_not_clean(session):
    """'anomalous' means the data is suspect — it must not count as clean,
    or a collapsed scraper could run the streak to 7 on garbage."""
    run_source(session, make_spec())
    src = _source(session)
    _add_run(session, src.id, 1, "anomalous")

    status = gate_status(session)
    assert status.streak == 1
    assert _day(status, _at(1).date().isoformat()).sources[
        "magicbricks/bangalore"] == "anomalous"


def test_new_source_does_not_retroactively_dirty_history(session):
    """Adding mysore today must not reset a bangalore streak to zero."""
    run_source(session, make_spec("bangalore"))
    blr = _source(session, "bangalore")
    for days_ago in range(1, 7):
        _add_run(session, blr.id, days_ago, "ok")
    # Mysore joins today only
    run_source(session, make_spec("mysore"))

    status = gate_status(session)
    assert status.streak == 7
    # Yesterday knows nothing about mysore; today requires both
    assert "magicbricks/mysore" not in _day(
        status, _at(1).date().isoformat()).sources
    assert set(_day(status, _at(0).date().isoformat()).sources) == {
        "magicbricks/bangalore", "magicbricks/mysore"}


def test_new_source_failing_today_does_break_today(session):
    run_source(session, make_spec("bangalore"))
    run_source(session, make_spec("mysore"))
    mys = _source(session, "mysore")
    session.execute(
        ScrapeRun.__table__.update()
        .where(ScrapeRun.source_id == mys.id)
        .values(status="failed"))
    session.commit()

    status = gate_status(session)
    assert status.streak == 0
    assert _day(status, _at(0).date().isoformat()).clean is False


def test_today_pending_does_not_break_an_existing_streak(session):
    """Between midnight IST and the 05:30 job, today has no runs yet. That is
    'pending', not dirty — otherwise the gate would read 0 every night."""
    run_source(session, make_spec())
    src = _source(session)
    # Push the source-creating run back to day 4, leaving today empty (it
    # can't simply be deleted — raw_payloads references it, raw-first by
    # design), then fill days 1-3.
    session.execute(
        ScrapeRun.__table__.update()
        .where(ScrapeRun.source_id == src.id)
        .values(started_at=_at(4), finished_at=_at(4)))
    session.commit()
    for days_ago in range(1, 4):
        _add_run(session, src.id, days_ago, "ok")

    status = gate_status(session)
    assert _day(status, _at(0).date().isoformat()).pending is True
    assert status.streak == 4          # yesterday back to day 4, uninterrupted


def test_source_dead_longer_than_the_window_still_breaks_the_gate(session):
    """Regression: a source with NO runs inside the lookback window must not
    silently drop out of the gate. Inferring 'first seen' from windowed runs
    made a totally dead scraper invisible and the gate reported CLEAN — the
    exact failure the project exists to catch."""
    run_source(session, make_spec("bangalore"))
    run_source(session, make_spec("mysore"))
    blr, mys = _source(session, "bangalore"), _source(session, "mysore")

    # Mysore's only run is 40 days ago — far outside the 14-day window.
    session.execute(
        ScrapeRun.__table__.update()
        .where(ScrapeRun.source_id == mys.id)
        .values(started_at=_at(40), finished_at=_at(40)))
    # Bangalore has collected cleanly every day since.
    for days_ago in range(1, 8):
        _add_run(session, blr.id, days_ago, "ok")
    session.commit()

    status = gate_status(session)
    assert status.streak == 0
    assert status.met is False
    today = _day(status, _at(0).date().isoformat())
    assert today.sources["magicbricks/mysore"] == "missing"
    assert today.clean is False


def test_partly_collected_today_is_pending_not_broken(session):
    """Regression: jobs are staggered (05:30 RERA, 06:00 portals). A check
    between them saw some sources 'missing' and reported the streak broken,
    then it repaired itself an hour later."""
    run_source(session, make_spec("bangalore"))
    run_source(session, make_spec("mysore"))
    blr, mys = _source(session, "bangalore"), _source(session, "mysore")
    for days_ago in range(1, 4):
        _add_run(session, blr.id, days_ago, "ok")
        _add_run(session, mys.id, days_ago, "ok")
    # Mysore has not run yet today — fold its run into day 1 so today is empty
    # for mysore (it can't be deleted; raw_payloads references it).
    session.execute(
        ScrapeRun.__table__.update()
        .where(ScrapeRun.source_id == mys.id,
               ScrapeRun.started_at >= _at(0, hour=0))
        .values(started_at=_at(1, hour=5), finished_at=_at(1, hour=5)))
    session.commit()

    status = gate_status(session)
    today = _day(status, _at(0).date().isoformat())
    assert today.pending is True
    assert today.sources["magicbricks/mysore"] == "missing"
    assert today.sources["magicbricks/bangalore"] == "ok"
    assert status.streak == 3           # days 1-3, not reset to 0


def test_a_real_failure_today_breaks_even_while_others_pend(session):
    """The grace above must not swallow an actual failure: a failed run today
    is dirty immediately, even if another source simply hasn't run yet."""
    run_source(session, make_spec("bangalore"))
    run_source(session, make_spec("mysore"))
    blr, mys = _source(session, "bangalore"), _source(session, "mysore")
    # Clear today for BOTH sources (a same-day 'ok' would legitimately rescue
    # the day), then give bangalore a failure today and mysore nothing.
    session.execute(
        ScrapeRun.__table__.update()
        .where(ScrapeRun.started_at >= _at(0, hour=0))
        .values(started_at=_at(1, hour=5), finished_at=_at(1, hour=5)))
    for days_ago in range(2, 4):
        _add_run(session, blr.id, days_ago, "ok")
        _add_run(session, mys.id, days_ago, "ok")
    _add_run(session, blr.id, 0, "failed", hour=6)
    session.commit()

    status = gate_status(session)
    today = _day(status, _at(0).date().isoformat())
    assert today.sources["magicbricks/bangalore"] == "failed"
    assert today.sources["magicbricks/mysore"] == "missing"
    assert today.pending is False       # a real failure is never 'in flight'
    assert status.streak == 0


def test_disabled_source_is_not_required(session):
    run_source(session, make_spec("bangalore"))
    run_source(session, make_spec("mysore"))
    mys = _source(session, "mysore")
    mys.enabled = False
    session.commit()

    status = gate_status(session)
    assert status.streak == 1
    assert "magicbricks/mysore" not in _day(
        status, _at(0).date().isoformat()).sources
