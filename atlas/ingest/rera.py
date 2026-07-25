"""Karnataka RERA project registry — Priority-1 source (plan.md §4).

Ported from the trial's validated collector (trial/sources/rera.py); the
fetch/parse logic is kept verbatim, retargeted at Postgres. Established facts
(handoff.md §7, do not relearn):

- `viewAllProjects` server-renders the ENTIRE registry (~6 MB, ~9.8k rows) in
  one GET. No login, no pagination.
- The data is four parallel JS arrays built by .push() calls, positionally
  aligned. parse() asserts equal lengths and fails loudly on mismatch.
- ~10% of rows are in-flight applications with no registration number — they
  are counted and skipped, not stored.
- The promoter is the accountable legal entity, NOT the marketing brand, and
  promoter names fragment badly — norm_promoter() is what makes builder track
  records meaningful. Builders are deduped on that normalised key.
- Statewide registry: Mysore projects ingest with zero extra work (§4a);
  district segmentation comes with the Phase-4 per-project detail pass.
"""
import html as html_lib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.ingest.pipeline import ANOMALY_VOLUME_RATIO, _trailing_avg_items
from atlas.models import Builder, RawPayload, ReraProject, ScrapeRun, Source

log = logging.getLogger(__name__)

SOURCE_NAME = "rera_karnataka"
CITY = "karnataka"  # statewide registry — not a single-market source
URL = "https://rera.karnataka.gov.in/viewAllProjects?language=en"
PARSER_VERSION = "rera/1.0.0"

# Browser UA: the site is a Java/Spring app that returns an error page to some
# default clients. One GET per day — well below any sane rate limit.
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

# Legal-suffix noise that splits one promoter into several ("Sobha Limited" /
# "SOBHA LIMITED", "&" / "AND"). Track record is meaningless if one builder
# counts as three.
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
    """Canonical registration number for cross-source joins.

    Portals prefix inconsistently ('TOR/PRM/KA/RERA/...'); anchoring on the
    PRM/KA/RERA substring lifted the listing join from 75% to 99.6%. Values
    with no recognisable number are returned uppercased-and-stripped so a
    caller can still compare them, rather than silently becoming None.
    """
    if not value:
        return None
    v = re.sub(r"\s+", "", str(value)).upper()
    m = _REG_RE.search(v)
    return m.group(1) if m else (v or None)


def fetch(timeout: float = 180.0) -> str:
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers=HEADERS) as client:
        r = client.get(URL)
        r.raise_for_status()
        return r.text


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
        # Values are HTML-escaped inside the JS string literals — without
        # unescaping, "ESS &amp; ESS" and "ESS AND ESS" are different promoters.
        rec = {f: html_lib.unescape(cols[f][i] or "").strip() for f in cols}
        rec["reg_no_canon"] = canon_reg_no(rec["reg_no"])
        rec["promoter_norm"] = norm_promoter(rec["promoter_name"])
        rec["parser_version"] = PARSER_VERSION
        out.append(rec)
    return out


@dataclass
class ReraRunResult:
    run_id: int
    status: str
    items_found: int
    new: int
    updated: int
    unregistered: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_source(session: Session) -> Source:
    source = session.scalar(
        select(Source).where(Source.name == SOURCE_NAME, Source.city == CITY)
    )
    if source is None:
        source = Source(name=SOURCE_NAME, city=CITY, kind="official",
                        fetcher=f"custom:{URL}", expected_daily_volume=9000)
        session.add(source)
        session.flush()
    return source


def _get_or_create_builder(session: Session, name: str,
                           name_norm: str, cache: dict) -> Builder:
    if name_norm in cache:
        return cache[name_norm]
    builder = session.scalar(select(Builder).where(Builder.name_norm == name_norm))
    if builder is None:
        builder = Builder(name=name, name_norm=name_norm)
        session.add(builder)
        session.flush()
    cache[name_norm] = builder
    return builder


def run(session: Session, html_override: str | None = None) -> ReraRunResult:
    """One RERA run: fetch → archive raw → parse → upsert builders + projects.

    html_override feeds an archived page instead of fetching — the offline
    test path and the recovery path when a parser fix replays history.
    """
    source = _get_or_create_source(session)
    scrape_run = ScrapeRun(source_id=source.id, status="running")
    session.add(scrape_run)
    session.commit()

    try:
        html = html_override if html_override is not None else fetch()
    except Exception as exc:
        scrape_run.status = "failed"
        scrape_run.error = f"fetch: {exc}"
        scrape_run.finished_at = _now()
        scrape_run.items_found = 0
        session.commit()
        log.exception("RERA fetch failed")
        return ReraRunResult(scrape_run.id, "failed", 0, 0, 0, 0)

    # Raw first: the ~6 MB page is archived (TOAST-compressed by Postgres)
    # before parsing, so a parser bug is recoverable rather than data loss.
    session.add(RawPayload(scrape_run_id=scrape_run.id, url=URL,
                           payload_text=html))
    session.commit()

    try:
        items = parse(html)
    except Exception as exc:
        scrape_run.status = "failed"
        scrape_run.error = f"parse: {exc}"
        scrape_run.finished_at = _now()
        scrape_run.items_found = 0
        session.commit()
        log.exception("RERA parse failed")
        return ReraRunResult(scrape_run.id, "failed", 0, 0, 0, 0)

    counters = {"new": 0, "updated": 0, "unregistered": 0}
    builder_cache: dict = {}
    try:
        for rec in items:
            reg = rec["reg_no_canon"]
            if not reg or not rec["reg_no"].strip():
                # In-flight application: no id to join on. Storing it as
                # registered would be exactly the wrong signal.
                counters["unregistered"] += 1
                continue

            builder = (
                _get_or_create_builder(session, rec["promoter_name"],
                                       rec["promoter_norm"], builder_cache)
                if rec["promoter_norm"] else None
            )
            project = session.scalar(
                select(ReraProject).where(ReraProject.rera_reg_no == reg)
            )
            if project is None:
                session.add(ReraProject(
                    rera_reg_no=reg,
                    builder_id=builder.id if builder else None,
                    project_name=rec["project_name"] or None,
                    raw=rec,
                ))
                counters["new"] += 1
            else:
                project.builder_id = builder.id if builder else project.builder_id
                project.project_name = rec["project_name"] or project.project_name
                project.raw = rec
                project.fetched_at = _now()
                counters["updated"] += 1
        session.commit()
    except Exception as exc:
        session.rollback()
        scrape_run.status = "failed"
        scrape_run.error = f"upsert: {exc}"
        scrape_run.items_found = len(items)
        scrape_run.finished_at = _now()
        session.commit()
        log.exception("RERA upsert failed (raw page is archived, re-parseable)")
        return ReraRunResult(scrape_run.id, "failed", len(items),
                             counters["new"], counters["updated"],
                             counters["unregistered"])

    # Volume-collapse guard: a shrunken page means truncation or shape change,
    # not that Karnataka deregistered 9,000 projects.
    avg = _trailing_avg_items(session, source.id, exclude_run_id=scrape_run.id)
    status = "ok"
    if avg is not None and len(items) < avg * ANOMALY_VOLUME_RATIO:
        status = "anomalous"
        scrape_run.error = f"{len(items)} rows vs trailing avg {avg:.0f}"
        log.warning("RERA anomalous run: %s", scrape_run.error)

    scrape_run.status = status
    scrape_run.items_found = len(items)
    scrape_run.finished_at = _now()
    session.commit()
    return ReraRunResult(scrape_run.id, status, len(items), counters["new"],
                         counters["updated"], counters["unregistered"])
