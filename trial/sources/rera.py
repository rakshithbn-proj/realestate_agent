"""Karnataka RERA project registry — the Priority 1 source in plan.md section 4.

Free, public, no login, no actor. `viewAllProjects` server-renders the *entire*
registry into one response (~6 MB, ~9.8k rows), so a single GET gets everything
and there is no pagination to walk.

The data is not in a table. The page embeds it as four parallel JavaScript arrays
built by successive .push() calls, positionally aligned one entry per project:

    applicationNameList   -> acknowledgement no.  ACK/KA/RERA/1251/308/PR/130726/010413
    applicationNameList2  -> registration no.     PRM/KA/RERA/1251/308/PR/170726/008819
    applicationNameList3  -> project name         THE ROOTS BY ELEGANCE INFRA
    applicationNameList4  -> promoter name        PIONIER DEVELOPMENTS PRIVATE LIMITED

They feed an autocomplete widget, which is incidental — positional alignment is
the contract we depend on, so parse() asserts equal lengths and the caller treats
a mismatch as a failed run rather than silently zipping truncated data.

Why this source earns its place: 98.5% of MagicBricks listings carrying a rera_id
resolve to a row here (measured against the day-1 trial data), which turns a bare
listing into project -> promoter -> legal entity. Note that the promoter is
routinely *not* the marketing brand — "Godrej Properties" listings resolve to
"GODREJ SSPDL GREEN ACRES PRIVATE LIMITED", "Prestige Estates Projects Ltd." to
"PRESTIGE PROJECTS PRIVATE LIMITED" — and the promoter is the entity actually
accountable for complaints, delays and litigation. That distinction is the whole
point of builder intelligence.
"""
import gzip
import html as html_lib
import json
import logging
import re
import sqlite3
import time

from .. import config, db

log = logging.getLogger("trial")

SOURCE = "rera"
URL = "https://rera.karnataka.gov.in/viewAllProjects?language=en"
PARSER_VERSION = "rera-v1"

# Browser UA: the site is a Java/Spring app that returns an error page to some
# default clients. Requests are one GET per day — well below any sane rate limit.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}

_PUSH = r"\s*\.\s*push\(\s*'([^']*)'\s*\)"
# (?![0-9]) stops `applicationNameList` from also matching `applicationNameList2`.
_ARRAYS = {
    "ack_no": "applicationNameList",
    "reg_no": "applicationNameList2",
    "project_name": "applicationNameList3",
    "promoter_name": "applicationNameList4",
}

_REG_RE = re.compile(r"(PRM/KA/RERA/[A-Z0-9/]+)")

# Legal-suffix noise that splits one promoter into several. The registry is
# free text typed by promoters, so the same entity appears as "Sobha Limited"
# and "SOBHA LIMITED" (66 + 55 projects), or "ESS & ESS ... PRIVATE LIMITED"
# and "ESS AND ESS ... PRIVATE LIMITED". Builder track record is meaningless if
# one builder counts as three, so store a normalised key alongside the display
# name — this mirrors builders.name_norm in docs/schema.sql.
_SUFFIXES = [
    "PRIVATE LIMITED", "PVT LTD", "PVT. LTD.", "PVT.LTD", "LIMITED", "LTD",
    "LLP", "INDIA", "AND COMPANY", "CO",
]


def norm_promoter(name: str | None) -> str | None:
    """Collapse casing, punctuation, '&'/'AND' and legal suffixes to one key."""
    if not name or not name.strip():
        return None
    s = html_lib.unescape(name).upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    changed = True
    while changed:                       # strip stacked suffixes: "... PVT LTD INDIA"
        changed = False
        for suf in _SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -len(suf) - 1].strip()
                changed = True
    return s or None


def canon_reg_no(value: str | None) -> str | None:
    """Canonical form of a RERA registration number for cross-source joins.

    Portals prefix the number inconsistently — MagicBricks emits
    'TOR/PRM/KA/RERA/1251/310/PR/250304/000047' while the registry holds
    'PRM/KA/RERA/...'. Anchoring on the PRM/KA/RERA substring lifted the join
    rate from 75.1% to 98.5% on day-1 data. Values with no recognisable
    registration number are returned uppercased-and-stripped so a caller can
    still compare them, rather than silently becoming None.
    """
    if not value:
        return None
    v = re.sub(r"\s+", "", str(value)).upper()
    m = _REG_RE.search(v)
    return m.group(1) if m else (v or None)


def fetch(timeout: float = 180.0) -> str:
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=True, headers=HEADERS) as client:
        r = client.get(URL)
        r.raise_for_status()
        return r.text


def archive_raw(html: str, run_id: int) -> str:
    """Gzip the source HTML to disk, keyed by run.

    plan.md section 7: raw before parsed, so a parser bug is recoverable rather
    than data loss. The 6 MB page is kept out of SQLite (it would dwarf every
    other row) but stays fully re-parseable — ~200 KB gzipped per day.
    """
    out_dir = config.DB_PATH.parent / "raw" / SOURCE
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run_id}.html.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(html)
    return str(path)


def parse(html: str) -> list[dict]:
    """Extract one record per project from the embedded JS arrays.

    Raises ValueError if the arrays disagree in length — that means the page
    shape changed and positional alignment can no longer be trusted. Failing
    loudly beats zipping mismatched columns into plausible-looking garbage.
    """
    cols = {
        field: re.findall(re.escape(var) + r"(?![0-9])" + _PUSH, html)
        for field, var in _ARRAYS.items()
    }
    lengths = {f: len(v) for f, v in cols.items()}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"RERA array length mismatch, page shape changed: {lengths}")

    n = next(iter(lengths.values()))
    if n == 0:
        raise ValueError("RERA page parsed to 0 projects — selectors are stale")

    out = []
    for i in range(n):
        # The values are HTML-escaped inside the JS string literals — without
        # unescaping, "ESS &amp; ESS" and "ESS AND ESS" are different promoters.
        rec = {f: html_lib.unescape(cols[f][i] or "").strip() for f in cols}
        rec["reg_no_canon"] = canon_reg_no(rec["reg_no"])
        rec["promoter_norm"] = norm_promoter(rec["promoter_name"])
        rec["parser_version"] = PARSER_VERSION
        out.append(rec)
    return out


def ingest(conn: sqlite3.Connection, run_id: int, items: list[dict]) -> dict:
    """Upsert into rera_projects. Registered projects only.

    ~10% of rows carry an acknowledgement number but a blank registration number
    — applications in flight, not yet registered. They are counted and skipped
    rather than stored: an un-registered project has no id to join on, and
    treating one as registered would be exactly the wrong signal.
    """
    now = db.now_iso()
    counters = {"new": 0, "updated": 0, "unregistered": 0}

    for rec in items:
        reg = rec["reg_no_canon"]
        if not reg or not rec["reg_no"].strip():
            counters["unregistered"] += 1
            continue

        row = conn.execute(
            "SELECT id FROM rera_projects WHERE rera_reg_no=?", (reg,)
        ).fetchone()
        payload = json.dumps(rec, ensure_ascii=False)

        if row is None:
            conn.execute(
                """INSERT INTO rera_projects (rera_reg_no, ack_no, project_name,
                       promoter_name, promoter_norm, raw, parser_version,
                       first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (reg, rec["ack_no"], rec["project_name"], rec["promoter_name"],
                 rec["promoter_norm"], payload, PARSER_VERSION, now, now),
            )
            counters["new"] += 1
        else:
            conn.execute(
                """UPDATE rera_projects SET ack_no=?, project_name=?, promoter_name=?,
                       promoter_norm=?, raw=?, parser_version=?, last_seen_at=? WHERE id=?""",
                (rec["ack_no"], rec["project_name"], rec["promoter_name"],
                 rec["promoter_norm"], payload, PARSER_VERSION, now, row["id"]),
            )
            counters["updated"] += 1

    conn.commit()
    return counters


def collect(conn: sqlite3.Connection) -> None:
    """One RERA run, mirroring scrape.scrape_source's envelope.

    Cost is recorded as $0.00 — deliberately, not as a placeholder. It is the
    number that makes the day-7 comparison against paid portal actors honest.
    """
    run_id = db.start_run(conn, SOURCE)
    t0 = time.monotonic()
    try:
        html = fetch()
        archive_raw(html, run_id)
        items = parse(html)
    except Exception as exc:  # noqa: BLE001 — a failed run is itself trial data
        log.error("%s: run failed: %s", SOURCE, exc)
        db.finish_run(conn, run_id, "failed", duration_s=round(time.monotonic() - t0, 1),
                      error=str(exc))
        return

    counters = ingest(conn, run_id, items)
    duration = time.monotonic() - t0

    # Same guard as the portals: a collapse in row count means the page changed
    # or was truncated, not that Karnataka deregistered 9,000 projects.
    anomalous = False
    avg = db.trailing_avg_items(conn, SOURCE)
    if avg is not None and len(items) < avg * config.ANOMALY_VOLUME_RATIO:
        anomalous = True
        log.warning("%s: %d rows vs trailing avg %.0f — anomalous run", SOURCE, len(items), avg)

    note = (f"new={counters['new']} updated={counters['updated']} "
            f"unregistered={counters['unregistered']}")
    db.finish_run(conn, run_id, "anomalous" if anomalous else "ok", items=len(items),
                  cost_usd=0.0, duration_s=round(duration, 1), note=note)
    log.info("%s: %s — %d rows, %s, %.0fs", SOURCE,
             "anomalous" if anomalous else "ok", len(items), note, duration)


def ingest_file(conn: sqlite3.Connection, path: str) -> None:
    """Re-parse an archived run from disk — offline test, and the recovery path
    when a parser fix needs replaying over history."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        html = fh.read()
    run_id = db.start_run(conn, SOURCE)
    items = parse(html)
    counters = ingest(conn, run_id, items)
    db.finish_run(conn, run_id, "ok", items=len(items), cost_usd=0.0, duration_s=0.0,
                  note=f"REPARSE new={counters['new']} updated={counters['updated']} "
                       f"unregistered={counters['unregistered']}")
    log.info("rera reparsed from %s: %s", path, counters)
