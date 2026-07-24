"""Daily scrape scheduler (IST), mirroring stockquery's APScheduler pattern.
max_instances=1 prevents overlapping cycles."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config

log = logging.getLogger(__name__)


def scrape_cycle() -> None:
    """Collect every source, then regenerate the markdown report."""
    from . import db, monitor
    from .scrape import get_token, scrape_source
    from .sources import rera

    conn = db.connect()
    try:
        # Official sources first: they need no token, so a missing/expired
        # APIFY_TOKEN degrades the cycle to portals-only instead of losing the
        # whole run. RERA is also the highest-value source (plan.md section 4).
        for name, collect in (("rera", rera.collect),):
            try:
                collect(conn)
            except Exception:  # noqa: BLE001 — one bad source must not stop the rest
                log.exception("scrape_cycle: %s failed", name)

        try:
            token = get_token()
        except SystemExit as exc:            # get_token raises SystemExit when unset
            log.error("scrape_cycle: skipping Apify sources — %s", exc)
        else:
            for source in config.SOURCES:
                try:
                    scrape_source(conn, source, token)
                except Exception:  # noqa: BLE001
                    log.exception("scrape_cycle: %s failed", source)

        monitor.write_report(conn)
    finally:
        conn.close()


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(
        scrape_cycle,
        CronTrigger(hour=config.SCRAPE_HOUR, minute=config.SCRAPE_MINUTE,
                    timezone=config.TIMEZONE),
        id="daily_scrape",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
