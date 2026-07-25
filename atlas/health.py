"""Source health — "no new listings" must be distinguishable from "the
scraper is dead" (plan §2). This summary feeds /sources now and the daily
report's source-health line in Phase 2.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import ScrapeRun, Source

CONSECUTIVE_FAILURE_ALERT = 2
SILENT_AFTER_HOURS = 36  # no run at all in this window → source is silent


@dataclass
class SourceHealth:
    source_id: int
    name: str
    city: str
    enabled: bool
    healthy: bool
    reason: str
    last_run_status: str | None
    last_run_at: str | None
    last_ok_at: str | None
    consecutive_bad_runs: int
    last_items_found: int | None
    expected_daily_volume: int | None


def source_health(session: Session) -> list[SourceHealth]:
    out = []
    now = datetime.now(timezone.utc)
    for source in session.scalars(select(Source).order_by(Source.name, Source.city)):
        runs = session.scalars(
            select(ScrapeRun)
            .where(ScrapeRun.source_id == source.id)
            .order_by(ScrapeRun.started_at.desc())
            .limit(200)   # count a long outage fully; still bounded
        ).all()

        last = runs[0] if runs else None
        # Most-recent 'ok' resolved independently of the window so an outage
        # longer than the window never reports last_ok_at as null.
        last_ok = session.scalar(
            select(ScrapeRun)
            .where(ScrapeRun.source_id == source.id, ScrapeRun.status == "ok")
            .order_by(ScrapeRun.started_at.desc())
            .limit(1)
        )
        consecutive_bad = 0
        for r in runs:
            if r.status in ("failed", "anomalous"):
                consecutive_bad += 1
            elif r.status == "ok":
                break

        stuck = (last is not None and last.status == "running"
                 and last.started_at < now - timedelta(hours=2))
        if not source.enabled:
            healthy, reason = True, "disabled"
        elif last is None:
            healthy, reason = False, "never ran"
        elif stuck:
            # Check before 'silent': a stuck run IS a recent run, just wedged —
            # reporting it as "no run" would hide the real failure mode.
            healthy, reason = False, "run stuck in 'running'"
        elif last.started_at < now - timedelta(hours=SILENT_AFTER_HOURS):
            healthy, reason = False, f"silent: no run in {SILENT_AFTER_HOURS}h"
        elif consecutive_bad >= CONSECUTIVE_FAILURE_ALERT:
            healthy, reason = False, f"{consecutive_bad} consecutive bad runs"
        else:
            healthy, reason = True, "ok"

        out.append(SourceHealth(
            source_id=source.id,
            name=source.name,
            city=source.city,
            enabled=source.enabled,
            healthy=healthy,
            reason=reason,
            last_run_status=last.status if last else None,
            last_run_at=last.started_at.isoformat() if last else None,
            last_ok_at=last_ok.started_at.isoformat() if last_ok else None,
            consecutive_bad_runs=consecutive_bad,
            last_items_found=last.items_found if last else None,
            expected_daily_volume=source.expected_daily_volume,
        ))
    return out


def source_health_dicts(session: Session) -> list[dict]:
    return [asdict(h) for h in source_health(session)]
