"""Human-readable rendering of scores. ASCII only — this also goes into logs.

Kept apart from engine.py so formatting choices can change without touching
the thing that judges.
"""
from sqlalchemy.orm import Session

from atlas.money import inr
from atlas.scoring.engine import ScoreRunResult
from atlas.scoring.weights import (
    ACTIVE_FACTORS,
    NO_DATA_FACTORS,
    WEIGHTS,
    WEIGHTS_VERSION,
)

# Buckets for the dry-run histogram. Deliberately coarse: the question this
# answers is "is the ranking discriminating at all, or is everything piled at
# one end?" — which is what the weights need calibrating against.
_BANDS = ((80, 100), (70, 80), (60, 70), (50, 60), (40, 50), (0, 40))


def format_score_run(result: ScoreRunResult) -> str:
    out: list[str] = []
    a = out.append
    a(f"DEAL SCORE  weights v{result.weights_version}  {result.score_date} (IST)")
    a(f"  scored {result.scored}   skipped {result.skipped} "
      f"(nothing known about them)   "
      f"{'DRY RUN - nothing written' if not result.written else 'written'}")
    if not result.results:
        a("")
        a("No listings scored. Let collection and tagging run first.")
        return "\n".join(out)

    scores = [s.overall for s in result.results]
    a("")
    a("DISTRIBUTION")
    width = max(len(result.results), 1)
    for lo, hi in _BANDS:
        n = sum(1 for s in scores if lo <= s < hi or (hi == 100 and s == 100))
        bar = "#" * int(40 * n / width)
        a(f"  {lo:>3}-{hi:<3} {n:>5}  {bar}")
    a(f"  median {sorted(scores)[len(scores) // 2]:.1f}   "
      f"min {min(scores):.1f}   max {max(scores):.1f}")

    # Coverage is the honest headline: how often each factor had anything to
    # say. A weighted factor that abstains most of the time is carrying its
    # weight in name only, and that is a calibration signal, not a detail.
    a("")
    a("FACTOR COVERAGE  (share of scored listings where the factor had data)")
    for name in ACTIVE_FACTORS:
        got = sum(1 for s in result.results
                  for f in s.factors if f.factor == name and f.value is not None)
        pct = 100.0 * got / len(result.results)
        a(f"  {name:<20} weight {WEIGHTS[name]:>3}   {pct:>5.1f}%"
          f"{'   <- abstains on most listings' if pct < 50 else ''}")
    a("")
    a("NOT SCORED - no data exists")
    for name, reason in NO_DATA_FACTORS.items():
        a(f"  {name:<20} {reason.splitlines()[0]}")
    return "\n".join(out)


def format_explain(session: Session, result: ScoreRunResult,
                   listing_id: int) -> str:
    from atlas.models import Listing

    scored = next((s for s in result.results if s.listing_id == listing_id), None)
    if scored is None:
        return (f"Listing {listing_id} was not scored: it is not active, or "
                "nothing is known about it yet (no price, no legal tags).")

    listing = session.get(Listing, listing_id)
    out: list[str] = []
    a = out.append
    a(f"LISTING {listing_id}  weights v{WEIGHTS_VERSION}")
    if listing is not None:
        price = f"Rs {inr(listing.price_inr)}" if listing.price_inr else "no price"
        a(f"  {listing.title or '(no title)'}")
        a(f"  {listing.city}  {listing.property_type or '?'}  {price}")
    a("")
    a(f"SCORE {scored.overall:.1f} / 100     coverage {scored.coverage * 100:.0f}%")
    a("")
    for f in scored.explain():
        if f.value is None:
            a(f"  {f.factor:<20}  ABSTAINED  (weight {f.weight} redistributed)")
        else:
            a(f"  {f.factor:<20} {f.contribution:>6.1f} / {f.weight:<3} "
              f"(raw {f.value:.2f})")
        for key, value in f.evidence.items():
            if key == "note":
                continue
            a(f"      {key}: {value}")
        if f.evidence.get("note"):
            a(f"      note: {f.evidence['note']}")
        a("")
    a("NOT SCORED - no data exists")
    for name in NO_DATA_FACTORS:
        row = next((r for r in scored.factors if r.factor == name), None)
        if row:
            a(f"  {name:<20} {row.evidence.get('reason', '')}")
    return "\n".join(out)


def format_top(rows: list[dict], reachable_only: bool = True) -> str:
    out: list[str] = []
    a = out.append
    scope = ("fundable today" if reachable_only
             else "ALL listings, including ones you cannot fund")
    a(f"TOP LISTINGS  ({scope})")
    if not rows:
        a("")
        a("Nothing to show. With --all this means no listing has been scored "
          "yet; without it, nothing scored is currently affordable.")
        return "\n".join(out)
    a(f"  {'score':>5} {'cov':>4} {'market':<10} {'locality':<20} "
      f"{'type':<12} {'price':>14} {'cash bar':>13}")
    for row in rows:
        price = inr(row["price_inr"], dash="on request")
        cash = inr(row["cash_needed_inr"])
        flag = "" if row["financeable"] else "  [cash only - legal flag]"
        a(f"  {row['overall']:>5.1f} {row['coverage'] * 100:>3.0f}% "
          f"{(row['city'] or '?'):<10} {(row['locality'] or '?')[:20]:<20} "
          f"{(row['property_type'] or '?')[:12]:<12} {price:>14} {cash:>13}{flag}")
        top = [f for f in row["factors"] if f["weight"] > 0][:3]
        detail = "  ".join(
            f"{f['factor']}={f['value'] * f['weight']:.0f}/{f['weight']}"
            for f in top
        )
        a(f"        {detail}")
    return "\n".join(out)
