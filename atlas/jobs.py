"""Daily ingestion jobs — shared by the APScheduler wiring and the CLI so a
cron/manual run and a scheduled run are the same code path (idempotent by
design, plan §7).
"""
import logging

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
        log.info("rera run %s: %s — %d rows (new=%d updated=%d unregistered=%d)",
                 result.run_id, result.status, result.items_found,
                 result.new, result.updated, result.unregistered)


def ingest_portals() -> None:
    for spec in SOURCES.values():
        with _session() as session:
            result = run_source(session, spec)
            log.info("%s/%s run %s: %s — %d items (new=%d updated=%d "
                     "price=%d relisted=%d failed=%d)",
                     spec.name, spec.city, result.run_id, result.status,
                     result.items_found, result.new, result.updated,
                     result.price_changed, result.relisted, result.failed)


def sweep_and_tag() -> None:
    settings = get_settings()
    with _session() as session:
        for spec in SOURCES.values():
            removed = sweep_stale_listings(session, spec,
                                           stale_days=settings.stale_after_days)
            log.info("sweep %s/%s: %d marked removed", spec.name, spec.city, removed)
        tag_result = legal.tag_listings(session)
        log.info("legal tags: %d listings, %d tags",
                 tag_result.tagged_listings, tag_result.tags_written)


def build_scheduler():
    """In-process APScheduler with every job explicitly in Asia/Kolkata."""
    from apscheduler.schedulers.background import BackgroundScheduler

    tz = get_settings().timezone
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(ingest_rera, "cron", hour=5, minute=30,
                      id="rera_daily", misfire_grace_time=3600)
    scheduler.add_job(ingest_portals, "cron", hour=6, minute=0,
                      id="portals_daily", misfire_grace_time=3600)
    scheduler.add_job(sweep_and_tag, "cron", hour=6, minute=45,
                      id="sweep_and_tag_daily", misfire_grace_time=3600)
    return scheduler
