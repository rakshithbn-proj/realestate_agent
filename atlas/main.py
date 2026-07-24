from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select, text

import atlas
from atlas.auth import require_token
from atlas.db import get_engine, make_session_factory
from atlas.models import ScrapeRun, Source

app = FastAPI(title="Atlas", version=atlas.__version__)


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
    factory = make_session_factory(get_engine())
    with factory() as session:
        rows = session.scalars(select(Source).order_by(Source.name, Source.city))
        return [
            {"id": s.id, "name": s.name, "city": s.city, "kind": s.kind,
             "enabled": s.enabled,
             "expected_daily_volume": s.expected_daily_volume}
            for s in rows
        ]


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
