"""Trial metrics: the numbers the week-end design-vs-pricing decision needs.

summary() feeds the dashboard's /api/summary; write_report() keeps a markdown
record in trial/reports/ after every scrape cycle.
"""
import sqlite3
from datetime import datetime, timezone

from . import config

COVERAGE_FIELDS = ["price_inr", "area_sqft", "lat", "locality", "project", "rera_id", "url"]


def _day_span(conn) -> tuple[str | None, int]:
    row = conn.execute("SELECT MIN(started_at) AS first FROM runs").fetchone()
    if not row or not row["first"]:
        return None, 0
    first = datetime.fromisoformat(row["first"])
    days = (datetime.now(timezone.utc) - first).days + 1
    return row["first"][:10], max(days, 1)


def source_stats(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for source in config.ALL_SOURCES:
        runs = conn.execute(
            "SELECT status, items, cost_usd, duration_s, started_at, note FROM runs "
            "WHERE source=? ORDER BY id DESC", (source,),
        ).fetchall()
        total = len(runs)
        ok = sum(1 for r in runs if r["status"] == "ok")
        anomalous = sum(1 for r in runs if r["status"] == "anomalous")
        failed = sum(1 for r in runs if r["status"] == "failed")
        # Two different questions, two different metrics:
        #   success_rate — did the actor run without erroring (ok + anomalous)?
        #   clean_rate   — did the run deliver usable data (ok only)?
        # Keeping only the first is what let acres99 report "100.0% success" on
        # day 1 while 596/600 of its items were empty stubs: both runs completed,
        # both were flagged anomalous, and the headline metric averaged that to
        # 100%. The go/no-go gate in _verdict() uses clean_rate for that reason.
        finished = ok + anomalous
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM listings WHERE source=? AND status='active'",
            (source,),
        ).fetchone()["c"]

        coverage = {}
        if active:
            for f in COVERAGE_FIELDS:
                c = conn.execute(
                    f"SELECT COUNT(*) AS c FROM listings WHERE source=? AND status='active' "
                    f"AND {f} IS NOT NULL", (source,),
                ).fetchone()["c"]
                coverage[f] = round(100.0 * c / active, 1)

        last = runs[0] if runs else None
        out.append({
            "source": source,
            "runs": total,
            "ok": ok,
            "anomalous": anomalous,
            "failed": failed,
            "success_rate": round(100.0 * finished / total, 1) if total else None,
            "clean_rate": round(100.0 * ok / total, 1) if total else None,
            "avg_items": round(sum(r["items"] or 0 for r in runs) / total, 1) if total else 0,
            "total_cost_usd": round(sum(r["cost_usd"] or 0 for r in runs), 4),
            "active_listings": active,
            "coverage_pct": coverage,
            "last_run": {
                "at": last["started_at"], "status": last["status"],
                "items": last["items"], "cost_usd": round(last["cost_usd"] or 0, 4),
                "duration_s": last["duration_s"], "note": last["note"],
            } if last else None,
        })
    return out


def change_stats(conn: sqlite3.Connection, limit: int = 15) -> dict:
    counts = {k: 0 for k in ("new", "price_changed", "removed", "relisted")}
    for r in conn.execute("SELECT kind, COUNT(*) AS c FROM changes GROUP BY kind"):
        counts[r["kind"]] = r["c"]
    drops = conn.execute(
        """SELECT c.observed_at, c.old_price, c.new_price, l.title, l.locality, l.url, l.source,
                  ROUND(100.0 * (c.new_price - c.old_price) / c.old_price, 1) AS pct
           FROM changes c JOIN listings l ON l.id = c.listing_id
           WHERE c.kind='price_changed' AND c.new_price < c.old_price
           ORDER BY pct ASC LIMIT ?""", (limit,),
    ).fetchall()
    return {"counts": counts, "top_price_drops": [dict(r) for r in drops]}


def parked_stats(conn: sqlite3.Connection) -> list[dict]:
    """Historical stats for sources removed from the daily cycle.

    Parked sources drop out of source_stats (it iterates config.SOURCES), which
    would silently erase the reason they were parked from the day-7 report. The
    trial's output is a decision, so the evidence behind it has to survive.
    """
    out = []
    for source in getattr(config, "PARKED_SOURCES", {}):
        runs = conn.execute(
            "SELECT status, items FROM runs WHERE source=?", (source,)
        ).fetchall()
        if not runs:
            continue
        raw_total = conn.execute(
            "SELECT COUNT(*) AS c FROM raw_items WHERE source=?", (source,)
        ).fetchone()["c"]
        raw_usable = conn.execute(
            "SELECT COUNT(*) AS c FROM raw_items WHERE source=? AND external_id IS NOT NULL",
            (source,),
        ).fetchone()["c"]
        out.append({
            "source": source,
            "runs": len(runs),
            "items_pushed": sum(r["items"] or 0 for r in runs),
            "raw_archived": raw_total,
            "raw_usable": raw_usable,
            "usable_pct": round(100.0 * raw_usable / raw_total, 1) if raw_total else 0.0,
        })
    return out


def rera_stats(conn: sqlite3.Connection) -> dict | None:
    """Registry size and the listing -> project join rate.

    The join rate is the metric that justifies the source: a rera_id on a
    listing is worthless unless it resolves to a registered project, and a
    listing citing a registration number absent from the registry is a risk
    signal in its own right, not a parsing miss.
    """
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM rera_projects").fetchone()["c"]
    except sqlite3.OperationalError:
        return None
    if not total:
        return None

    from .sources.rera import canon_reg_no

    registry = {r["rera_reg_no"] for r in
                conn.execute("SELECT rera_reg_no FROM rera_projects")}
    rows = conn.execute(
        "SELECT rera_id FROM listings WHERE status='active' AND rera_id IS NOT NULL "
        "AND TRIM(rera_id) <> ''"
    ).fetchall()
    matched = sum(1 for r in rows if canon_reg_no(r["rera_id"]) in registry)
    promoters = conn.execute(
        "SELECT COUNT(DISTINCT promoter_norm) AS c FROM rera_projects "
        "WHERE promoter_norm IS NOT NULL"
    ).fetchone()["c"]
    return {
        "registered_projects": total,
        "distinct_promoters": promoters,
        "listings_with_rera_id": len(rows),
        "listings_joined": matched,
        "join_rate_pct": round(100.0 * matched / len(rows), 1) if rows else None,
        "unmatched": len(rows) - matched,
    }


def summary(conn: sqlite3.Connection) -> dict:
    first_day, days = _day_span(conn)
    sources = source_stats(conn)
    total_cost = round(sum(s["total_cost_usd"] for s in sources), 4)
    per_day = round(total_cost / days, 4) if days else 0.0
    return {
        "trial_started": first_day,
        "trial_day": days,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cost": {
            "total_usd": total_cost,
            "per_day_usd": per_day,
            "projected_30d_usd": round(per_day * 30, 2),
        },
        "sources": sources,
        "rera": rera_stats(conn),
        "parked": parked_stats(conn),
        "changes": change_stats(conn),
        "verdict": _verdict(sources, per_day),
    }


def _verdict(sources: list[dict], per_day: float) -> dict:
    """Machine-checkable flags for the week-end decision."""
    rates = [s["clean_rate"] for s in sources if s["clean_rate"] is not None]
    reliable = bool(rates) and min(rates) >= 90.0
    coverage_ok = all(
        (s["coverage_pct"].get("price_inr", 0) >= 95 and s["coverage_pct"].get("area_sqft", 0) >= 80)
        for s in sources if s["active_listings"]
    )
    return {
        "actors_reliable_so_far": reliable,
        "field_coverage_ok": coverage_ok,
        "projected_monthly_apify_usd": round(per_day * 30, 2),
        "guide": (
            "After 7 days: if all success rates >=90%, coverage holds, and projected cost is "
            "acceptable -> free actors are fine, proceed to Phase 1 on this stack. "
            "If a source is unreliable -> swap in its paid fallback (see plan.md section 4). "
            "If volumes are anomalous or fields are missing -> revisit before building more."
        ),
    }


def write_report(conn: sqlite3.Connection) -> str:
    s = summary(conn)
    lines = [
        "# Trial — Apify reliability & cost monitor",
        f"\nDay **{s['trial_day']}** of trial (started {s['trial_started']}) — generated {s['generated_at']}",
        f"\n## Cost\n",
        f"- Total so far: **${s['cost']['total_usd']}**",
        f"- Per day: ${s['cost']['per_day_usd']}",
        f"- Projected 30-day: **${s['cost']['projected_30d_usd']}**",
        "\n## Sources\n",
        "| Source | Runs | OK | Anom | Fail | Ran % | Clean % | Avg items | Active listings | Cost $ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for src in s["sources"]:
        lines.append(
            f"| {src['source']} | {src['runs']} | {src['ok']} | {src['anomalous']} | "
            f"{src['failed']} | {src['success_rate'] or '—'} | {src['clean_rate'] or '—'} | "
            f"{src['avg_items']} | {src['active_listings']} | {src['total_cost_usd']} |"
        )
    lines.append("\n*Ran % = actor completed (ok + anomalous). "
                 "**Clean % = run delivered usable data (ok only)** — this is the go/no-go gate.*")

    if s["parked"]:
        lines.append("\n## Parked sources (removed from the daily cycle)\n")
        lines.append("| Source | Runs | Items pushed | Raw archived | Usable | Usable % |")
        lines.append("|---|---|---|---|---|---|")
        for p in s["parked"]:
            lines.append(
                f"| {p['source']} | {p['runs']} | {p['items_pushed']} | {p['raw_archived']} | "
                f"{p['raw_usable']} | **{p['usable_pct']}%** |"
            )
        lines.append("\nSee `PARKED_SOURCES` in trial/config.py for why each was parked "
                     "and what to re-test after day 7.")
    lines.append("\n## Field coverage (% of active listings)\n")
    lines.append("| Source | " + " | ".join(COVERAGE_FIELDS) + " |")
    lines.append("|---|" + "---|" * len(COVERAGE_FIELDS))
    for src in s["sources"]:
        cov = src["coverage_pct"]
        lines.append(f"| {src['source']} | " + " | ".join(str(cov.get(f, "—")) for f in COVERAGE_FIELDS) + " |")
    if s["rera"]:
        r = s["rera"]
        lines.append(
            f"\n## Karnataka RERA registry\n\n"
            f"- Registered projects: **{r['registered_projects']:,}** "
            f"across {r['distinct_promoters']:,} distinct promoters (normalised)\n"
            f"- Listings carrying a RERA id: {r['listings_with_rera_id']}\n"
            f"- Joined to a registered project: **{r['listings_joined']} "
            f"({r['join_rate_pct']}%)**\n"
            f"- Cited a registration number absent from the registry: "
            f"**{r['unmatched']}** — risk-flag candidates, not parse failures"
        )
    c = s["changes"]["counts"]
    lines.append(
        f"\n## Changes observed\n\n- New: {c['new']}  |  Price changes: {c['price_changed']}  |  "
        f"Removed: {c['removed']}  |  Relisted: {c['relisted']}"
    )
    if s["changes"]["top_price_drops"]:
        lines.append("\n### Top price drops\n")
        for d in s["changes"]["top_price_drops"][:10]:
            lines.append(f"- {d['pct']}%  ₹{d['old_price']:,} → ₹{d['new_price']:,}  "
                         f"{d['title'] or ''} ({d['locality'] or '?'}, {d['source']})")
    lines.append(f"\n## Decision guide\n\n{s['verdict']['guide']}\n")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / "trial-summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
