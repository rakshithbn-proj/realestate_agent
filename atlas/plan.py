"""Capital Plan — the runway to the first deal (roadmap Phase 2b).

Before you can transact there is a hard cash floor: stamp duty and
registration cannot be borrowed, and the down payment is cash. Ranking
properties you cannot fund trains you to ignore the briefing, so until the
floor is cleared the useful output is different:

    how much do I need, for what, and how long at my savings rate

This computes that against **real listings already collected**, not a guessed
price. The rungs are the cheapest genuinely-reachable properties per market,
so the target is something that exists rather than an abstraction.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.ingest.legal import legal_tags_by_listing
from atlas.models import Listing, Locality
from atlas.profile import InvestorProfile, default_profile, is_financeable


@dataclass
class Rung:
    """One reachable entry point: a real listing and what it takes to close."""
    city: str
    corridor: str
    locality: str | None
    property_type: str | None
    price_inr: int
    cash_needed_inr: int
    financeable: bool
    months_away: int | None          # None = savings never catch up
    months_away_if_market_runs: int | None
    unlock_needed_inr: int           # committed capital to liquidate today
    unlock_tax_inr: int
    listing_id: int
    url: str | None


@dataclass
class CapitalPlan:
    deployable_inr: int
    reserved_inr: int
    committed_inr: int
    monthly_contribution_inr: int
    ceiling_now_inr: int
    ceiling_with_unlock_inr: int
    considered: int
    rungs: list[Rung] = field(default_factory=list)


def build_plan(session: Session, profile: InvestorProfile | None = None,
               per_market: int = 3,
               market_appreciation: float = 0.10) -> CapitalPlan:
    """Cheapest reachable entry points per market, with the runway to each.

    Only in-corridor, priced, active listings count — an unreachable or
    off-thesis property is not a rung on the ladder, and a listing with no
    price cannot be planned against at all.
    """
    profile = profile or default_profile()
    tags = legal_tags_by_listing(session)
    localities = {loc.id: loc.name for loc in session.scalars(select(Locality))}

    candidates: list[Rung] = []
    considered = 0
    for listing in session.scalars(
        select(Listing).where(Listing.status.in_(("active", "relisted")))
    ):
        considered += 1
        if listing.price_inr is None:
            continue
        corridor = profile.corridor_for(
            listing.city, localities.get(listing.locality_id))
        if corridor is None:
            continue

        price = float(listing.price_inr)
        financeable = is_financeable(tags.get(listing.id))
        unlock = profile.unlock_needed_for(price, financeable)
        candidates.append(Rung(
            city=listing.city,
            corridor=corridor,
            locality=localities.get(listing.locality_id),
            property_type=listing.property_type,
            price_inr=int(price),
            cash_needed_inr=profile.cash_needed(price, financeable),
            financeable=financeable,
            months_away=profile.months_until_affordable(price, financeable),
            months_away_if_market_runs=profile.months_until_affordable(
                price, financeable, annual_appreciation=market_appreciation),
            unlock_needed_inr=unlock,
            unlock_tax_inr=profile.unlock_cost(unlock) if unlock else 0,
            listing_id=listing.id,
            url=listing.url,
        ))

    # Cheapest first within each market, so the ladder starts at the lowest
    # real bar rather than the most attractive property.
    by_market: dict[str, list[Rung]] = {}
    for rung in sorted(candidates, key=lambda r: r.cash_needed_inr):
        by_market.setdefault(rung.city, []).append(rung)
    rungs = [r for market in by_market.values() for r in market[:per_market]]
    rungs.sort(key=lambda r: r.cash_needed_inr)

    # What committed capital buys, net of the tax on liquidating it.
    unlockable = max(0, profile.committed_inr
                     - profile.unlock_cost(profile.committed_inr))
    with_unlock = InvestorProfile(
        liquid_total_inr=profile.liquid_total_inr + unlockable,
        reserved_inr=profile.reserved_inr,
        monthly_contribution_inr=profile.monthly_contribution_inr,
        ltv=profile.ltv,
    )
    return CapitalPlan(
        deployable_inr=profile.deployable_inr,
        reserved_inr=profile.reserved_inr,
        committed_inr=profile.committed_inr,
        monthly_contribution_inr=profile.monthly_contribution_inr,
        ceiling_now_inr=profile.max_price_for(),
        ceiling_with_unlock_inr=with_unlock.max_price_for(),
        considered=considered,
        rungs=rungs,
    )


def format_plan(plan: CapitalPlan) -> str:
    """Human-readable plan. ASCII only — this also goes into logs."""
    out: list[str] = []
    a = out.append
    a("YOUR POSITION")
    a(f"  Deployable now      Rs {plan.deployable_inr:>12,}")
    a(f"  Reserved (untouched)Rs {plan.reserved_inr:>12,}")
    if plan.committed_inr:
        a(f"  Committed (unlockable) Rs {plan.committed_inr:>9,}")
    a(f"  Saving              Rs {plan.monthly_contribution_inr:>12,} / month")
    a(f"  Ceiling today       Rs {plan.ceiling_now_inr:>12,}")
    if plan.ceiling_with_unlock_inr > plan.ceiling_now_inr:
        a(f"  Ceiling with unlock Rs {plan.ceiling_with_unlock_inr:>12,}")
    a("")

    if not plan.rungs:
        a(f"No in-corridor priced listings among {plan.considered} active.")
        a("Nothing to plan against yet - let collection run.")
        return "\n".join(out)

    a(f"THE LADDER  (cheapest real entry points, {plan.considered} listings considered)")
    a(f"  {'market':<10} {'locality':<22} {'price':>10} {'cash bar':>10}  when")
    for r in plan.rungs:
        if r.months_away is None:
            when = "never at this savings rate"
        elif r.months_away == 0:
            when = "NOW"
        else:
            when = f"{r.months_away} mo"
            if (r.months_away_if_market_runs is not None
                    and r.months_away_if_market_runs != r.months_away):
                when += f"  ({r.months_away_if_market_runs} mo if mkt +10%/yr)"
            elif r.months_away_if_market_runs is None:
                when += "  (never if mkt +10%/yr)"
        flag = "" if r.financeable else "  [cash only - legal flag]"
        a(f"  {r.city:<10} {(r.locality or '?')[:22]:<22} "
          f"{r.price_inr:>10,} {r.cash_needed_inr:>10,}  {when}{flag}")

    nearest = plan.rungs[0]
    a("")
    a(f"NEAREST BAR  Rs {nearest.cash_needed_inr:,} "
      f"({nearest.locality or '?'}, {nearest.city})")
    if nearest.unlock_needed_inr and plan.committed_inr:
        a(f"  Unlock Rs {nearest.unlock_needed_inr:,} of committed holdings to "
          f"close today; tax approx Rs {nearest.unlock_tax_inr:,}.")
    a("  Stamp duty and registration cannot be borrowed - this is cash.")
    return "\n".join(out)
