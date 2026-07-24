"""Single-snapshot 'what's actionable today' cut over the trial data.

Not a price-drop signal — that needs multiple days of runs (see plan.md Phase 1).
This ranks listings by how far their ₹/sqft sits below their own locality's
median ₹/sqft, using only the most recent scrape. Localities with too few
listings to form a meaningful median are reported separately, not scored.
"""
import sqlite3
import statistics

from . import config

MIN_COMPS = 3
TOPSHEET_DISCOUNT_THRESHOLD = 15.0


def build_topsheet(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """SELECT source, title, locality, price_inr, area_sqft, lister_kind, url
           FROM listings
           WHERE status='active' AND price_inr > 0 AND area_sqft > 0"""
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        locality = (r["locality"] or "").strip()
        if not locality:
            continue
        item = {
            "source": r["source"],
            "title": r["title"],
            "locality": locality,
            "price_inr": r["price_inr"],
            "area_sqft": r["area_sqft"],
            "ppsqft": r["price_inr"] / r["area_sqft"],
            "lister_kind": r["lister_kind"],
            "url": r["url"],
        }
        groups.setdefault(locality.lower(), []).append(item)

    scored: list[dict] = []
    skipped: list[tuple[str, int]] = []
    for key, items in groups.items():
        if len(items) < MIN_COMPS:
            skipped.append((items[0]["locality"], len(items)))
            continue
        median = statistics.median(i["ppsqft"] for i in items)
        for i in items:
            discount = (median - i["ppsqft"]) / median * 100
            if discount >= TOPSHEET_DISCOUNT_THRESHOLD:
                scored.append({**i, "locality_median_ppsqft": median, "discount_pct": discount})

    scored.sort(key=lambda x: x["discount_pct"], reverse=True)
    skipped.sort(key=lambda x: x[0].lower())
    return {"scored": scored, "skipped": skipped}


def write_topsheet(conn: sqlite3.Connection) -> str:
    data = build_topsheet(conn)
    lines = [
        "# Topsheet — below-locality-median listings",
        "",
        "> Single-snapshot relative pricing only — **not** a price-drop signal. "
        "Price-history-based detection (5%/10% reductions, days-on-market) needs "
        "multiple days of runs (see plan.md Phase 1). This ranks each listing's "
        "₹/sqft against the median ₹/sqft of other active listings in the same "
        "locality, from the latest scrape only.",
        "",
        f"Threshold: >= {TOPSHEET_DISCOUNT_THRESHOLD:.0f}% below locality median ₹/sqft. "
        f"Localities need >= {MIN_COMPS} active listings to be scored.",
        "",
        "## Candidates",
        "",
    ]
    if data["scored"]:
        lines.append("| Discount | Locality | Title | Source | Price (₹) | Area (sqft) | ₹/sqft | Locality median ₹/sqft | Lister | URL |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for i in data["scored"]:
            url_cell = f"[link]({i['url']})" if i["url"] else "—"
            lines.append(
                f"| {i['discount_pct']:.1f}% | {i['locality']} | {i['title'] or '—'} | "
                f"{i['source']} | {i['price_inr']:,} | {i['area_sqft']:.0f} | "
                f"{i['ppsqft']:.0f} | {i['locality_median_ppsqft']:.0f} | "
                f"{i['lister_kind'] or '—'} | {url_cell} |"
            )
    else:
        lines.append("_None above the threshold in the latest snapshot._")

    lines.append("")
    lines.append("## Localities skipped (insufficient comps)")
    lines.append("")
    if data["skipped"]:
        lines.append("| Locality | Listings |")
        lines.append("|---|---|")
        for locality, count in data["skipped"]:
            lines.append(f"| {locality} | {count} |")
    else:
        lines.append("_None — every locality had enough listings to score._")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / "topsheet.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
