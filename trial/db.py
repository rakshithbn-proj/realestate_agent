"""SQLite storage for the trial. Zero external dependencies."""
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',   -- ok | failed | anomalous
    items INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    duration_s REAL,
    error TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS raw_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    source TEXT NOT NULL,
    external_id TEXT,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_run ON raw_items(run_id);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT, project TEXT, developer TEXT, locality TEXT,
    property_type TEXT, bhk INTEGER, area_sqft REAL,
    price_inr INTEGER, price_per_sqft REAL,
    lat REAL, lon REAL, rera_id TEXT,
    lister_kind TEXT, contact_name TEXT,
    url TEXT, posted_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',    -- active | removed
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    removed_at TEXT,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(source, status);

-- Karnataka RERA registry (trial/sources/rera.py). Column names mirror the
-- designed table in docs/schema.sql so the Postgres migration stays a port:
-- district / declared_completion / status / complaints_count live on the
-- per-project detail page (one POST each, ~8.8k of them) and are deliberately
-- left for a later pass — the registry list alone already carries the join key.
CREATE TABLE IF NOT EXISTS rera_projects (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rera_reg_no    TEXT NOT NULL UNIQUE,     -- canonical PRM/KA/RERA/... form
    ack_no         TEXT,
    project_name   TEXT,
    promoter_name  TEXT,                     -- legal entity, often != marketing brand
    promoter_norm  TEXT,                     -- dedup key; maps to builders.name_norm
    raw            TEXT NOT NULL,
    parser_version TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rera_promoter ON rera_projects(promoter_norm);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    kind TEXT NOT NULL,                       -- new | price_changed | removed | relisted
    old_price INTEGER,
    new_price INTEGER,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_kind ON changes(kind, observed_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added to tables that already exist in a live trial.db. CREATE TABLE
# IF NOT EXISTS silently does nothing for an existing table, so new columns need
# an explicit ALTER or they are missing at runtime with no error until a query
# fails. Alembic handles this properly after the Postgres migration; until then
# this keeps a running trial upgradable without dropping collected data.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("rera_projects", "promoter_norm", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, coltype in MIGRATIONS:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        if not exists:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, now_iso())
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, items: int = 0, cost_usd: float = 0.0,
               duration_s: float | None = None, error: str | None = None,
               note: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, status=?, items=?, cost_usd=?, duration_s=?, error=?, note=? WHERE id=?",
        (now_iso(), status, items, cost_usd, duration_s, error, note, run_id),
    )
    conn.commit()


def trailing_avg_items(conn, source: str, n_runs: int = 5) -> float | None:
    rows = conn.execute(
        "SELECT items FROM runs WHERE source=? AND status='ok' ORDER BY id DESC LIMIT ?",
        (source, n_runs),
    ).fetchall()
    if not rows:
        return None
    return sum(r["items"] for r in rows) / len(rows)
