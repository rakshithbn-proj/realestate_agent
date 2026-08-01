"""Daily ingestion jobs — shared by the APScheduler wiring and the CLI so a
cron/manual run and a scheduled run are the same code path (idempotent by
design, plan §7).
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from atlas.config import get_settings
from atlas.db import get_engine, make_session_factory
from atlas.ingest import legal, rera
from atlas.ingest.pipeline import run_source, sweep_stale_listings
from atlas.ingest.registry import SOURCES

log = logging.getLogger(__name__)


def _session():
    return make_session_factory(get_engine())()


def ingest_rera() -> None:
    with _session() as session:
        result = rera.run(session)
        log.info("rera run %s: %s - %d rows (new=%d updated=%d unregistered=%d)",
                 result.run_id, result.status, result.items_found,
                 result.new, result.updated, result.unregistered)


def ingest_portal(name: str) -> None:
    """Run a single portal source by name."""
    if name not in SOURCES:
        raise KeyError(f"unknown portal source '{name}'; known: {list(SOURCES)}")
    spec = SOURCES[name]
    with _session() as session:
        result = run_source(session, spec)
        log.info("%s/%s run %s: %s - %d items (new=%d updated=%d "
                 "price=%d relisted=%d failed=%d)",
                 spec.name, spec.city, result.run_id, result.status,
                 result.items_found, result.new, result.updated,
                 result.price_changed, result.relisted, result.failed)


def ingest_portals() -> None:
    """Run every portal source. One portal raising must not skip the rest —
    otherwise a single dead actor costs every source ordered after it a day of
    ingestion (and, with the gate counting clean days, the whole streak)."""
    errors = 0
    for name in SOURCES:
        try:
            ingest_portal(name)
        except Exception:
            errors += 1
            log.exception("portal %r failed; continuing with the rest", name)
    if errors:
        log.error("%d/%d portal(s) failed", errors, len(SOURCES))


def sweep_and_tag() -> None:
    settings = get_settings()
    with _session() as session:
        for spec in SOURCES.values():
            removed = sweep_stale_listings(session, spec,
                                           stale_days=settings.stale_after_days)
            log.info("sweep %s/%s: %d marked removed", spec.name, spec.city, removed)
        # Tag only listings seen within the stale window — bounds nightly work
        # to recently-touched listings instead of re-stamping the whole table
        # (long-dead 'removed' listings can't change and are skipped).
        tag_result = legal.tag_recent_listings(
            session, since_days=settings.stale_after_days)
        log.info("legal tags: %d listings, %d tags",
                 tag_result.tagged_listings, tag_result.tags_written)


def run_daily() -> int:
    """The whole daily sequence in one call, for cron / Task Scheduler.

    Every step is attempted even if an earlier one raises: a RERA outage must
    not also cost us the portal runs (and the sweep), or one dead source would
    silently take the whole day's ingestion — and the Phase-1 gate — with it.
    Per-source failures are already recorded as run rows by run_source(); this
    only guards against hard errors escaping a step. Returns the number of
    steps that raised, so a scheduled run can exit non-zero and be noticed.
    """
    steps: list[tuple[str, callable]] = [("rera", ingest_rera)]
    steps += [(name, lambda n=name: ingest_portal(n)) for name in SOURCES]
    steps.append(("sweep_and_tag", sweep_and_tag))

    errors = 0
    for label, step in steps:
        try:
            step()
        except Exception:
            errors += 1
            log.exception("daily step %r failed; continuing", label)
    if errors:
        log.error("daily run finished with %d failed step(s)", errors)
    else:
        log.info("daily run finished cleanly (%d steps)", len(steps))
    return errors


# The daily schedule, held as data so the scheduler and the startup catch-up
# below can never disagree about when the collection window is. All times are
# Asia/Kolkata (plan §7); the scheduler is constructed with that timezone.
SCHEDULE: tuple[tuple[str, object, int, int], ...] = (
    ("rera_daily", ingest_rera, 5, 30),
    ("portals_daily", ingest_portals, 6, 0),
    ("sweep_and_tag_daily", sweep_and_tag, 6, 45),
)


def build_scheduler():
    """In-process APScheduler with every job explicitly in Asia/Kolkata."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone=get_settings().timezone)
    for job_id, func, hour, minute in SCHEDULE:
        scheduler.add_job(func, "cron", hour=hour, minute=minute,
                          id=job_id, misfire_grace_time=3600)
    return scheduler


def catch_up_if_missed() -> bool:
    """On startup, run the daily sequence if today's window has already passed
    and today is not clean yet. Returns True if a catch-up run happened.

    This exists because APScheduler here uses the default IN-MEMORY jobstore.
    If the process is down at 05:30, that fire is not "missed and retried" —
    on boot `add_job` computes next_run_time from now, so today's run is simply
    never scheduled and the day is lost silently. `misfire_grace_time` does NOT
    cover this: it only applies while the scheduler process itself was alive.

    Without this, any restart or maintenance window spanning 05:30-06:45 IST
    costs a Phase-1 gate day for no reason. Guarded so it cannot loop: once
    today is clean, further restarts do nothing.
    """
    from atlas.gate import gate_status

    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz)
    opens_hour, opens_minute = min((h, m) for _, _, h, m in SCHEDULE)
    if (now.hour, now.minute) < (opens_hour, opens_minute):
        return False        # today's window hasn't opened; the scheduler has it

    with _session() as session:
        today = gate_status(session).days[-1]
    if today.clean:
        return False        # already collected today — nothing to catch up

    log.warning(
        "startup catch-up: %s is not clean (%s) and the %02d:%02d window has "
        "passed - running the daily sequence now",
        today.day, today.sources or "no runs yet", opens_hour, opens_minute)
    run_daily()
    return True


def _catch_up_safely() -> None:
    """Thread target: a crash here must never take down app startup, but it
    must also never vanish silently."""
    try:
        catch_up_if_missed()
    except Exception:
        log.exception("startup catch-up failed; the scheduler still runs "
                      "tomorrow, but today may stay dirty")
