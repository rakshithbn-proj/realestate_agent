"""FastAPI app: JSON API + single-page dashboard + scheduler lifecycle.

Mirrors the stockquery architecture. Auth: set DASHBOARD_TOKEN in .env; requests
must carry it as an X-Auth-Token header (or ?token=). Empty token disables auth
for local dev.

Run locally:   uvicorn trial.main:app --reload --port 8010
In Docker:     docker compose up -d   (from the trial/ directory)
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config, db, monitor
from .scheduler import build_scheduler, scrape_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

_run_lock = threading.Lock()
_run_state = {"running": False, "last_error": None}

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect().close()  # ensure schema exists
    scheduler = None
    if config.SCHEDULER_ENABLED:
        scheduler = build_scheduler()
        scheduler.start()
        log.info("scheduler started: daily scrape %02d:%02d %s",
                 config.SCRAPE_HOUR, config.SCRAPE_MINUTE, config.TIMEZONE)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="RealEstate Trial Monitor", lifespan=lifespan)


def _check_auth(request: Request) -> None:
    if not config.DASHBOARD_TOKEN:
        return
    token = request.headers.get("x-auth-token") or request.query_params.get("token")
    if token != config.DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing token")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    _check_auth(request)
    return HTMLResponse(TEMPLATE.read_text(encoding="utf-8"))


@app.get("/api/summary")
def api_summary(request: Request):
    _check_auth(request)
    conn = db.connect()
    try:
        return monitor.summary(conn)
    finally:
        conn.close()


@app.get("/api/runs")
def api_runs(request: Request, limit: int = 50):
    _check_auth(request)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (min(limit, 500),)
        ).fetchall()
        return {"runs": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/changes")
def api_changes(request: Request, kind: str | None = None, limit: int = 100):
    _check_auth(request)
    conn = db.connect()
    try:
        q = ("SELECT c.*, l.title, l.locality, l.source, l.url FROM changes c "
             "JOIN listings l ON l.id = c.listing_id ")
        params: list = []
        if kind:
            q += "WHERE c.kind=? "
            params.append(kind)
        q += "ORDER BY c.id DESC LIMIT ?"
        params.append(min(limit, 1000))
        return {"changes": [dict(r) for r in conn.execute(q, params).fetchall()]}
    finally:
        conn.close()


def _background_scrape() -> None:
    if not _run_lock.acquire(blocking=False):
        return
    _run_state["running"] = True
    _run_state["last_error"] = None
    try:
        scrape_cycle()
    except Exception as exc:  # noqa: BLE001
        log.exception("manual scrape failed")
        _run_state["last_error"] = str(exc)
    finally:
        _run_state["running"] = False
        _run_lock.release()


@app.post("/api/run-now")
def api_run_now(request: Request, background_tasks: BackgroundTasks):
    _check_auth(request)
    if _run_state["running"]:
        return {"started": False, "reason": "a scrape cycle is already running"}
    background_tasks.add_task(_background_scrape)
    return {"started": True}


@app.get("/api/run-status")
def api_run_status(request: Request):
    _check_auth(request)
    return dict(_run_state)
