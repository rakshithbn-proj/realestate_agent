"""Run Apify actors, ingest results, track changes.

Reliability principles carried over from plan.md even in the trial:
- raw payloads stored before parsing (re-parseable forever)
- anomalous runs (volume collapse) never mark listings as removed
- every run's cost and duration recorded for the week-end decision
"""
import json
import logging
import os
import sqlite3
import time
from datetime import timedelta

from . import config, db
from .normalize import normalize

log = logging.getLogger("trial")


def get_token(cli_token: str | None = None) -> str:
    token = cli_token or os.environ.get("APIFY_TOKEN")
    if not token and config.TOKEN_FILE.exists():
        token = config.TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(
            "No Apify token. Set APIFY_TOKEN, pass --token, or put it in trial/apify_token.txt\n"
            "Get one free at https://console.apify.com -> Settings -> API & Integrations"
        )
    return token


def run_actor(token: str, actor: str, run_input: dict) -> tuple[list[dict], float, float]:
    """Execute an actor synchronously. Returns (items, cost_usd, duration_s).

    apify-client >=3.0 renamed `timeout_secs: int` -> `wait_duration: timedelta`
    and changed the returned `Run` from a dict to a Pydantic model (extra='allow',
    camelCase JSON aliases but snake_case attributes) — so attribute access
    replaces .get()/[...] throughout this function.
    """
    from apify_client import ApifyClient  # lazy: fixture mode needs no install

    client = ApifyClient(token)
    t0 = time.monotonic()
    run = client.actor(actor).call(run_input=run_input, wait_duration=timedelta(seconds=1800))
    duration = time.monotonic() - t0
    if run is None or run.status != "SUCCEEDED":
        status = run.status if run else "NO_RUN"
        raise RuntimeError(f"actor run did not succeed: {status}")

    cost = run.usage_total_usd
    if cost is None:
        cu = (run.stats.compute_units if run.stats else None) or 0.0
        cost = cu * config.USD_PER_COMPUTE_UNIT

    items = list(client.dataset(run.default_dataset_id).iterate_items())
    return items, float(cost or 0.0), duration


def ingest(conn: sqlite3.Connection, run_id: int, source: str, items: list[dict]) -> dict:
    """Store raw items, upsert listings, record changes. Returns counters."""
    now = db.now_iso()
    counters = {"new": 0, "price_changed": 0, "relisted": 0, "unparsed": 0}
    seen_ids: set[str] = set()

    for raw in items:
        norm = normalize(raw, source)
        conn.execute(
            "INSERT INTO raw_items (run_id, source, external_id, payload, fetched_at) VALUES (?,?,?,?,?)",
            (run_id, source, norm["external_id"], json.dumps(raw, ensure_ascii=False), now),
        )
        if norm["external_id"] is None:
            counters["unparsed"] += 1
            continue
        seen_ids.add(norm["external_id"])

        existing = conn.execute(
            "SELECT id, price_inr, status FROM listings WHERE source=? AND external_id=?",
            (source, norm["external_id"]),
        ).fetchone()

        if existing is None:
            cur = conn.execute(
                """INSERT INTO listings (source, external_id, title, project, developer,
                       locality, property_type, bhk, area_sqft, price_inr, price_per_sqft,
                       lat, lon, rera_id, lister_kind, contact_name, url, posted_at,
                       first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source, norm["external_id"], norm["title"], norm["project"],
                 norm["developer"], norm["locality"], norm["property_type"], norm["bhk"],
                 norm["area_sqft"], norm["price_inr"], norm["price_per_sqft"], norm["lat"],
                 norm["lon"], norm["rera_id"], norm["lister_kind"], norm["contact_name"],
                 norm["url"], norm["posted_at"], now, now),
            )
            conn.execute(
                "INSERT INTO changes (listing_id, run_id, kind, new_price, observed_at) VALUES (?,?,?,?,?)",
                (cur.lastrowid, run_id, "new", norm["price_inr"], now),
            )
            counters["new"] += 1
        else:
            if existing["status"] == "removed":
                conn.execute(
                    "INSERT INTO changes (listing_id, run_id, kind, new_price, observed_at) VALUES (?,?,?,?,?)",
                    (existing["id"], run_id, "relisted", norm["price_inr"], now),
                )
                counters["relisted"] += 1
            old_price, new_price = existing["price_inr"], norm["price_inr"]
            if new_price is not None and old_price is not None and new_price != old_price:
                conn.execute(
                    "INSERT INTO changes (listing_id, run_id, kind, old_price, new_price, observed_at) VALUES (?,?,?,?,?,?)",
                    (existing["id"], run_id, "price_changed", old_price, new_price, now),
                )
                counters["price_changed"] += 1
            conn.execute(
                """UPDATE listings SET title=?, project=?, developer=?, locality=?,
                       property_type=?, bhk=?, area_sqft=?,
                       price_inr=COALESCE(?, price_inr), price_per_sqft=?,
                       lat=?, lon=?, rera_id=?, lister_kind=?, contact_name=?, url=?,
                       posted_at=COALESCE(?, posted_at),
                       status='active', removed_at=NULL, last_seen_at=?
                   WHERE id=?""",
                (norm["title"], norm["project"], norm["developer"], norm["locality"],
                 norm["property_type"], norm["bhk"], norm["area_sqft"], norm["price_inr"],
                 norm["price_per_sqft"], norm["lat"], norm["lon"], norm["rera_id"],
                 norm["lister_kind"], norm["contact_name"], norm["url"], norm["posted_at"],
                 now, existing["id"]),
            )

    conn.commit()
    return counters | {"seen": len(seen_ids)}


def mark_removed(conn: sqlite3.Connection, run_id: int, source: str, seen: int) -> tuple[int, bool]:
    """Mark active listings not seen this run as removed — unless the run volume
    collapsed vs the trailing average (anomaly guard). Returns (removed, anomalous)."""
    avg = db.trailing_avg_items(conn, source)
    if avg is not None and seen < avg * config.ANOMALY_VOLUME_RATIO:
        log.warning("%s: %d items vs trailing avg %.0f — anomalous run, skipping removals",
                    source, seen, avg)
        return 0, True

    now = db.now_iso()
    rows = conn.execute(
        """SELECT l.id FROM listings l WHERE l.source=? AND l.status='active'
           AND l.id NOT IN (
               SELECT li.id FROM listings li
               JOIN raw_items ri ON ri.source = li.source AND ri.external_id = li.external_id
               WHERE ri.run_id=?
           )""",
        (source, run_id),
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE listings SET status='removed', removed_at=? WHERE id=?", (now, r["id"]))
        conn.execute(
            "INSERT INTO changes (listing_id, run_id, kind, observed_at) VALUES (?,?,?,?)",
            (r["id"], run_id, "removed", now),
        )
    conn.commit()
    return len(rows), False


def scrape_source(conn: sqlite3.Connection, source: str, token: str) -> None:
    cfg = config.SOURCES[source]
    run_id = db.start_run(conn, source)
    log.info("%s: starting actor %s", source, cfg["actor"])
    try:
        items, cost, duration = run_actor(token, cfg["actor"], cfg["input"])
    except Exception as exc:  # noqa: BLE001 — every failure is a data point in the trial
        log.error("%s: run failed: %s", source, exc)
        db.finish_run(conn, run_id, "failed", error=str(exc))
        return

    counters = ingest(conn, run_id, source, items)
    removed, anomalous = mark_removed(conn, run_id, source, counters["seen"])
    unparsed_ratio = counters["unparsed"] / len(items) if items else 0.0
    if unparsed_ratio > config.ANOMALY_UNPARSED_RATIO:
        anomalous = True
        log.warning("%s: %d/%d items unparsed (%.0f%%) — anomalous run",
                    source, counters["unparsed"], len(items), unparsed_ratio * 100)
    status = "anomalous" if anomalous else "ok"
    note = (f"new={counters['new']} price_changed={counters['price_changed']} "
            f"relisted={counters['relisted']} removed={removed} unparsed={counters['unparsed']}")
    db.finish_run(conn, run_id, status, items=len(items), cost_usd=cost,
                  duration_s=round(duration, 1), note=note)
    log.info("%s: %s — %d items, $%.4f, %.0fs", source, status, len(items), cost, duration)


def ingest_fixture(conn: sqlite3.Connection, source: str, fixture_path) -> None:
    """Feed a saved fixture through the exact same ingest path (offline test)."""
    items = json.loads(open(fixture_path, encoding="utf-8").read())
    run_id = db.start_run(conn, source)
    counters = ingest(conn, run_id, source, items)
    removed, anomalous = mark_removed(conn, run_id, source, counters["seen"])
    status = "anomalous" if anomalous else "ok"
    note = (f"FIXTURE new={counters['new']} price_changed={counters['price_changed']} "
            f"relisted={counters['relisted']} removed={removed} unparsed={counters['unparsed']}")
    db.finish_run(conn, run_id, status, items=len(items), cost_usd=0.0, duration_s=0.0, note=note)
    log.info("fixture ingested: %s", note)
