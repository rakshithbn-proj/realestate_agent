"""Phase-1 runtime gate: consecutive clean ingestion days.

Phase 1's done-when is a *runtime* gate, not a code gate (handoff §6): "7
consecutive clean ingestion days into Postgres". This measures it off the
`scrape_runs` history so the answer is evidence, not a claim.

A day is **clean** when every enabled source that was live that day landed at
least one `ok` run in it. Definitions that matter:

- **Days are Asia/Kolkata days** (settings.timezone), not UTC — a 05:30 IST job
  is the previous UTC day, so counting in UTC would split one morning's runs
  across two buckets and never show a streak.
- **A retry rescues the day.** A source that fails at 06:00 and succeeds at
  07:00 still ingested that day; the gate asks "did the data land", and the
  failed run is still on the record in health/`/sources`.
- **A source only counts from its own first run.** Adding a source (e.g.
  mysore) must not retroactively dirty history it could not have participated
  in — otherwise every new source resets the clock to zero.
- **Today never breaks the streak until it has run.** Before the morning jobs
  fire, today is `pending`, not dirty; only an actual bad run that day breaks
  it. Otherwise the gate would read 0 every night after midnight IST.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.config import get_settings
from atlas.models import ScrapeRun, Source

REQUIRED_CLEAN_DAYS = 7

# Worst-first: what to report for a source-day holding several run statuses.
_STATUS_RANK = ("failed", "anomalous", "running", "ok")


@dataclass
class DayStatus:
    day: str
    clean: bool
    pending: bool          # today, before any run has landed
    sources: dict[str, str] = field(default_factory=dict)


@dataclass
class GateStatus:
    required_days: int
    streak: int
    met: bool
    today: str
    days: list[DayStatus]


def _source_label(source: Source) -> str:
    return f"{source.name}/{source.city}"


def gate_status(session: Session, required_days: int = REQUIRED_CLEAN_DAYS,
                window_days: int = 14) -> GateStatus:
    tz = ZoneInfo(get_settings().timezone)
    today = datetime.now(tz).date()
    # Look back far enough to show context around the streak, and pull runs
    # from one extra day so a run near the IST/UTC boundary isn't clipped.
    window = max(window_days, required_days)
    earliest = today - timedelta(days=window)

    sources = list(session.scalars(select(Source).order_by(Source.name, Source.city)))
    runs = list(session.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.started_at >= datetime.combine(
            earliest - timedelta(days=1), datetime.min.time(), tzinfo=tz))
        .order_by(ScrapeRun.started_at)
    ))

    # (source_id, IST date) -> statuses seen
    by_day: dict[tuple[int, date], list[str]] = {}
    for run in runs:
        day = run.started_at.astimezone(tz).date()
        by_day.setdefault((run.source_id, day), []).append(run.status)

    # First run per source across ALL history, deliberately NOT derived from
    # the windowed runs above. If a source has been dead longer than the
    # window it has no runs in `runs` at all, and inferring "first seen" from
    # that would make it look not-yet-live — so it would be dropped from every
    # day and the gate would cheerfully report CLEAN for a totally dead
    # scraper. That is the exact failure this project exists to catch.
    first_run_day: dict[int, date] = {
        source_id: started.astimezone(tz).date()
        for source_id, started in session.execute(
            select(ScrapeRun.source_id, func.min(ScrapeRun.started_at))
            .group_by(ScrapeRun.source_id)
        ).all()
        if started is not None
    }

    days: list[DayStatus] = []
    for offset in range(window, -1, -1):
        day = today - timedelta(days=offset)
        per_source: dict[str, str] = {}
        for source in sources:
            if not source.enabled:
                continue
            started = first_run_day.get(source.id)
            if started is None or day < started:
                continue          # source wasn't live yet — not its day to fail
            statuses = by_day.get((source.id, day), [])
            if not statuses:
                per_source[_source_label(source)] = "missing"
            elif "ok" in statuses:
                per_source[_source_label(source)] = "ok"
            else:
                per_source[_source_label(source)] = next(
                    (s for s in _STATUS_RANK if s in statuses), statuses[0])
        clean = bool(per_source) and all(v == "ok" for v in per_source.values())
        # Today is 'pending' while the morning sequence is still in flight:
        # some sources have not reported yet, but none has actually failed.
        # The jobs are staggered (05:30 RERA, 06:00 portals, 06:45 sweep), so
        # without this a check at 05:45 would report the streak as broken and
        # then see it repair itself an hour later. Only a genuinely bad run
        # today breaks the streak. Past days get no such grace.
        pending = (day == today and not clean
                   and all(v in ("ok", "missing", "running")
                           for v in per_source.values()))
        days.append(DayStatus(day=day.isoformat(), clean=clean,
                              pending=pending, sources=per_source))

    # Count back from the most recent day, skipping a still-pending today.
    streak = 0
    for entry in reversed(days):
        if entry.pending:
            continue
        if entry.clean:
            streak += 1
        else:
            break

    return GateStatus(
        required_days=required_days,
        streak=streak,
        met=streak >= required_days,
        today=today.isoformat(),
        days=days,
    )


def gate_status_dict(session: Session, required_days: int = REQUIRED_CLEAN_DAYS) -> dict:
    status = gate_status(session, required_days=required_days)
    return asdict(status)
