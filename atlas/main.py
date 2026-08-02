from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select, text

import atlas
from atlas.auth import require_token
from atlas.config import get_settings
from atlas.db import get_engine, make_session_factory
from atlas.gate import gate_status_dict
from atlas.health import source_health_dicts
from atlas.models import ScrapeRun


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if get_settings().atlas_enable_scheduler:
        import threading

        from atlas.jobs import _catch_up_safely, build_scheduler
        scheduler = build_scheduler()
        scheduler.start()
        # Recover a day lost to downtime (container restart, host maintenance,
        # a `docker compose down` spanning the 05:30-06:45 IST window). Runs in
        # a daemon thread so a ~4-minute ingestion never blocks app startup or
        # the /health endpoint.
        threading.Thread(target=_catch_up_safely, name="atlas-catchup",
                         daemon=True).start()
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Atlas", version=atlas.__version__, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok,
            "version": atlas.__version__}


api = APIRouter(dependencies=[Depends(require_token)])


@api.get("/sources")
def list_sources() -> list[dict]:
    """Per-source health: last run, consecutive failures, silence detection."""
    factory = make_session_factory(get_engine())
    with factory() as session:
        return source_health_dicts(session)


@api.get("/gate")
def phase1_gate() -> dict:
    """Phase-1 runtime gate: consecutive clean ingestion days (handoff §6).

    Exposed over the API so the streak can be checked against the deployed
    instance without shelling into the VPS.
    """
    factory = make_session_factory(get_engine())
    with factory() as session:
        return gate_status_dict(session)


@api.get("/scores/top")
def top_scores(
    limit: int = Query(default=20, ge=1, le=100),
    city: str | None = None,
    include_unreachable: bool = False,
) -> list[dict]:
    """Ranked listings with the full factor decomposition.

    Defaults to what can actually be funded today (roadmap Phase 2b); pass
    `include_unreachable=true` for market context.
    """
    from atlas.scoring.engine import latest_scores

    factory = make_session_factory(get_engine())
    with factory() as session:
        return latest_scores(session, limit=limit, city=city,
                             reachable_only=not include_unreachable)


@api.get("/runs")
def recent_runs(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
    factory = make_session_factory(get_engine())
    with factory() as session:
        rows = session.scalars(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit)
        )
        return [
            {"id": r.id, "source_id": r.source_id, "status": r.status,
             "items_found": r.items_found,
             "started_at": r.started_at.isoformat() if r.started_at else None,
             "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "error": r.error}
            for r in rows
        ]


app.include_router(api)
