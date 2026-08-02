"""The individual factors. Pure functions, each returning a value and its cited
evidence — or `None` to abstain.

**Abstention is the load-bearing idea here.** A factor with no data for a
listing returns None; it does NOT return 0. Scoring 0 for "unknown" punishes a
listing for Atlas's own gaps, and with three factors currently dataless and a
price history days deep, that would push every score toward the floor and make
the ranking meaningless. Instead the engine renormalises over the weight that
was actually covered and records `coverage` on the score.

Every evidence dict names what it read. Where the underlying data is a seller's
*claim* rather than a verified fact, the evidence says so — the same separation
atlas/ingest/legal.py enforces between the RERA registry join (a fact) and
khata/layout keywords in listing text (a claim).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from atlas.models import Listing, PriceEvent
from atlas.profile import InvestorProfile, is_financeable

# --- tunables, all of them reasoned rather than measured ---------------------
# These are the four constants the plan flags for calibration against the real
# score distribution before weights v1 is treated as settled.

MIN_COMPS = 5             # fewer same-class comps than this and we abstain
PRICE_BAND = 0.15         # +/-15% around the locality median spans the 0..1 range
DOM_FLOOR_DAYS = 30       # below this, age says nothing
DOM_CEILING_DAYS = 180    # at/above this, the listing is fully "stale"
DROP_FULL_MARKS = 0.15    # a 15% fall off the peak scores the depth component 1.0
DROP_REPEAT_FULL = 3      # three separate reductions scores repetition 1.0

# Within-factor weights for `distress`. Each sub-component abstains
# independently, and the factor renormalises over whichever have data.
_DISTRESS_PARTS = {"drop_depth": 0.5, "drop_repetition": 0.2, "days_on_market": 0.3}


@dataclass(frozen=True)
class FactorResult:
    """A factor's contribution.

    `value is None` means the factor abstained. A factor may still return
    evidence alongside a None value when it knows *why* it had nothing to say
    — "the portal published no posting date and there is no price history yet"
    is a far more useful line in a decomposition than a bare "no data".
    Returning `None` instead of a FactorResult is the shorthand for the plain
    case where there is nothing more to explain.
    """

    value: float | None    # 0..1 before weighting; None = abstained
    evidence: dict[str, Any]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def asset_class(property_type: str | None) -> str | None:
    """'land' or 'built' — the only comparison that makes sense for Rs/sqft.

    Land and built stock are priced on different bases (a plot at Rs 4,600/sqft
    and a flat at Rs 8,000/sqft are not comparable), so a locality median that
    mixes them is meaningless. Without this split, every plot would read as a
    ~45% discount the day the plot source lands and would dominate the ranking
    on an artefact.
    """
    if not property_type:
        return None
    t = property_type.strip().lower()
    if any(k in t for k in ("plot", "land", "site")):
        return "land"
    if any(k in t for k in ("apartment", "flat", "floor", "villa", "house",
                            "penthouse", "studio")):
        return "built"
    return None


# ---------------------------------------------------------------- legal_risk

_RERA_BASE = {"pass": 1.0, "unknown": 0.6, "flag": 0.2, "fail": 0.0}

# Multipliers applied on top of the RERA base. Flags are severe because these
# are the conditions that make a property un-financeable and, in the revenue-
# site case, potentially unbuildable. Claimed passes give only a modest lift:
# the listing text saying "A khata" is the seller's word, not a document.
_CLAIM_MULTIPLIERS = {
    ("khata_type", "flag"): 0.25,
    ("khata_type", "pass"): 1.15,
    ("layout_approval", "flag"): 0.25,
    ("layout_approval", "pass"): 1.15,
    ("jurisdiction", "flag"): 0.70,
    ("jurisdiction", "pass"): 1.05,
}


def legal_risk(listing: Listing, tags: dict[str, str],
               tag_details: dict[str, dict] | None = None) -> FactorResult | None:
    """Legal-catastrophe avoidance — the heaviest factor by design.

    Abstains when the listing has not been tagged at all: an untagged listing
    is unexamined, not clean, and scoring it as either would be a lie.
    """
    if not tags:
        return None

    rera_status = tags.get("rera_registered", "unknown")
    value = _RERA_BASE.get(rera_status, 0.6)

    applied: list[str] = []
    for item in ("khata_type", "layout_approval", "jurisdiction"):
        status = tags.get(item)
        mult = _CLAIM_MULTIPLIERS.get((item, status))
        if mult is not None:
            value *= mult
            applied.append(f"{item}={status} (x{mult})")

    details = tag_details or {}
    return FactorResult(
        value=_clamp(value),
        evidence={
            "kind": "listing_legal_tags",
            "note": (
                "rera_registered is a verified registry join. khata_type / "
                "layout_approval / jurisdiction are keyword matches on the "
                "listing's own text — the SELLER's claim, NOT "
                "document-verified. Document-verified checks are the "
                "property-scoped legal_checks table (Phase 3+)."
            ),
            "rera_registered": rera_status,
            "tags": {item: tags.get(item) for item in
                     ("rera_registered", "khata_type", "jurisdiction",
                      "layout_approval")},
            "tag_ids": {item: d.get("id") for item, d in details.items()},
            "details": {item: d.get("detail") for item, d in details.items()
                        if d.get("detail")},
            "multipliers_applied": applied,
            "financeable": is_financeable(tags),
        },
    )


# -------------------------------------------------------------- capital_fit

# Months-away -> score. Stepped rather than continuous because the decision it
# informs is stepped: "this cycle", "this year", "not this plan".
_MONTHS_BANDS = ((0, 1.0), (6, 0.75), (12, 0.55), (18, 0.35), (24, 0.20))


def capital_fit(listing: Listing, tags: dict[str, str],
                profile: InvestorProfile) -> FactorResult | None:
    """Can this actually be bought, and if not, how far away is it?

    All arithmetic comes from atlas/profile.py — stamp duty and registration
    are cash on top of the down payment, and a legal flag collapses the ticket
    to cash because banks will not lend against it. Abstains on a listing with
    no price: 'price on request' must never sneak past a capital filter as
    affordable-by-default.
    """
    if listing.price_inr is None:
        return None

    price = float(listing.price_inr)
    financeable = is_financeable(tags)
    cash_needed = profile.cash_needed(price, financeable)
    months = profile.months_until_affordable(price, financeable)

    if profile.is_affordable(price, financeable):
        value, months = 1.0, 0
    elif months is None:
        # Savings never catch the market at this price. Decision-relevant, not
        # an error (handoff §9.1) — it means "buy smaller or further out NOW".
        value = 0.0
    else:
        value = next((v for limit, v in _MONTHS_BANDS if months <= limit), 0.05)

    return FactorResult(
        value=_clamp(value),
        evidence={
            "kind": "capital_model",
            "profile_version": profile.version,
            "price_inr": int(price),
            "cash_needed_inr": cash_needed,
            "deployable_inr": profile.deployable_inr,
            "monthly_contribution_inr": profile.monthly_contribution_inr,
            "ceiling_now_inr": profile.max_price_for(financeable),
            "financeable": financeable,
            "months_away": months,
            "note": (
                "cash_needed = down payment + Karnataka stamp duty + "
                "registration. Stamp duty and registration CANNOT be borrowed. "
                "months_away null means savings never catch the price."
                if not financeable else
                "cash_needed = down payment + Karnataka stamp duty + "
                "registration; the latter two cannot be borrowed."
            ),
        },
    )


# -------------------------------------------------------- price_vs_locality

def price_vs_locality(
    listing: Listing,
    medians: dict[tuple[str | None, int | None, str], tuple[float, int]],
) -> FactorResult | None:
    """Rs/sqft against same-class comps in the same locality.

    This is the honest STAND-IN for the guidance-value gap, not the thing
    itself. atlas_roadmap §4.8 means the gap between the *statutory* guidance
    value and the market price — a legally-anchored arbitrage signal. This
    measures only how a listing is priced against its peers, which moves with
    the same sentiment it is trying to detect. Guidance values were never
    built (handoff §9.8); `guidance_value_gap` stays a declared no-data factor
    and this does not stand in for it in the decomposition.

    Abstains without a price per sqft, without a resolvable asset class, or
    with fewer than MIN_COMPS same-class comps — a median over three promoted
    listings is noise wearing a number.
    """
    cls = asset_class(listing.property_type)
    if cls is None or listing.price_per_sqft is None:
        return None

    key = (listing.city, listing.locality_id, cls)
    found = medians.get(key)
    if found is None:
        return None
    median, comps = found
    if comps < MIN_COMPS or not median:
        return None

    ppsf = float(listing.price_per_sqft)
    ratio = ppsf / median
    # ratio 0.85 -> 1.0, ratio 1.00 -> 0.5, ratio 1.15 -> 0.0
    value = _clamp((1 + PRICE_BAND - ratio) / (2 * PRICE_BAND))

    return FactorResult(
        value=value,
        evidence={
            "kind": "locality_comparison",
            "note": (
                "peer-relative pricing within one micro-market. NOT the "
                "guidance-value gap (that data was never built) — this moves "
                "with the same market sentiment it is measuring."
            ),
            "price_per_sqft": round(ppsf, 2),
            "median_price_per_sqft": round(median, 2),
            "ratio": round(ratio, 4),
            "comps": comps,
            "asset_class": cls,
            "locality_id": listing.locality_id,
            "city": listing.city,
        },
    )


# ---------------------------------------------------------------- thesis_fit

# overall_plan.md §1 is explicit that buy-and-hold flats are not the game:
# the thesis is land and value creation. A flat is not disqualified, it is
# ranked below a plot of equal quality.
_TYPE_BASE = {"land": 1.0, "built": 0.35}
_BUILDER_FLOOR_HINTS = ("builder-floor", "builder floor", "independent")
_LISTER_ADJUST = {"owner": 0.15, "builder": -0.10, "broker": 0.0}
OFF_CORRIDOR_MULTIPLIER = 0.5


def thesis_fit(listing: Listing, profile: InvestorProfile,
               locality_name: str | None) -> FactorResult | None:
    """Alignment with the stated investment thesis: land over flats,
    owner-direct over brokered, inside a target corridor.

    Owner-direct is not a preference, it is a value signal: no broker in the
    middle, the seller is contactable, and the price is negotiable with the
    person who actually decides.
    """
    cls = asset_class(listing.property_type)
    if cls is None:
        return None

    value = _TYPE_BASE[cls]
    ptype = (listing.property_type or "").lower()
    if cls == "built" and any(h in ptype for h in _BUILDER_FLOOR_HINTS):
        value = 0.55       # more land content and more control than a flat

    lister = (listing.lister_kind or "").lower()
    value += _LISTER_ADJUST.get(lister, 0.0)

    corridor = profile.corridor_for(listing.city or "", locality_name)
    if corridor is None:
        value *= OFF_CORRIDOR_MULTIPLIER

    return FactorResult(
        value=_clamp(value),
        evidence={
            "kind": "thesis_alignment",
            "property_type": listing.property_type,
            "asset_class": cls,
            "lister_kind": listing.lister_kind,
            "corridor": corridor,
            "city": listing.city,
            "locality": locality_name,
            "note": (
                "land is weighted above built stock because overall_plan.md §1 "
                "rejects buy-and-hold as the strategy; owner-direct listings "
                "score higher because the decision-maker is reachable."
            ),
        },
    )


# ------------------------------------------------------------------ distress

def _now() -> datetime:
    return datetime.now(timezone.utc)


def distress(listing: Listing, events: list[PriceEvent],
             now: datetime | None = None) -> FactorResult | None:
    """Motivated-seller signals: price cuts and time on market.

    Each sub-component abstains independently and the factor renormalises over
    whichever have data. That matters right now: collection started
    2026-08-01, so most listings have neither a price history nor a portal
    `posted_at` yet, and this factor correctly abstains entirely rather than
    reporting every listing as un-distressed. It strengthens on its own as
    history accumulates — no code change needed.

    Days-on-market prefers the PORTAL's `posted_at`. Falling back to
    `first_seen_at` measures how long Atlas has been watching, not how long the
    listing has been unsold; the evidence always records which was used.
    """
    now = now or _now()
    parts: dict[str, float] = {}
    ev: dict[str, Any] = {"kind": "distress_signals"}

    drops = [e for e in events
             if e.pct_change is not None and float(e.pct_change) < 0]
    priced = [e for e in events if e.new_price is not None]
    if priced:
        peak = max(float(e.new_price) for e in priced)
        current = float(priced[-1].new_price)
        depth = (peak - current) / peak if peak > 0 else 0.0
        parts["drop_depth"] = _clamp(depth / DROP_FULL_MARKS)
        parts["drop_repetition"] = _clamp(len(drops) / DROP_REPEAT_FULL)
        ev.update({
            "price_event_ids": [e.id for e in events],
            "peak_price_inr": int(peak),
            "current_price_inr": int(current),
            "drop_from_peak_pct": round(depth * 100, 2),
            "reductions": len(drops),
        })

    reference = listing.posted_at or listing.first_seen_at
    dom_source = "posted_at" if listing.posted_at else "first_seen_at"
    if listing.posted_at is not None and reference is not None:
        days = (now - reference).days
        parts["days_on_market"] = _clamp(
            (days - DOM_FLOOR_DAYS) / (DOM_CEILING_DAYS - DOM_FLOOR_DAYS)
        )
        ev.update({"days_on_market": days, "days_on_market_source": dom_source,
                   "posted_at": reference.isoformat()})
    else:
        # No portal date: first_seen_at would report "days since Atlas
        # noticed", which is bounded by the first collection day and reads
        # every listing as brand new. Abstain on this component instead.
        ev["days_on_market_source"] = None
        ev["days_on_market_note"] = (
            "abstained: the portal published no posting date, and first_seen_at "
            "measures how long Atlas has watched, not how long it has been for "
            "sale. Backfillable from raw_payloads via `atlas.cli reparse`."
        )

    if not parts:
        # Abstain, but say why: "no posting date and no price history yet" is
        # a fact about Atlas's coverage, and it is the line that explains why
        # this factor is near-inert in the first weeks of collection.
        ev["abstained"] = ("no price history and no portal posting date — "
                           "nothing observed about this listing's age or "
                           "price movement yet")
        return FactorResult(value=None, evidence=ev)

    covered = sum(_DISTRESS_PARTS[k] for k in parts)
    value = sum(parts[k] * _DISTRESS_PARTS[k] for k in parts) / covered
    ev["components"] = {k: round(v, 4) for k, v in parts.items()}
    ev["component_coverage"] = round(covered, 4)
    return FactorResult(value=_clamp(value), evidence=ev)


# --------------------------------------------------------- seller_motivation

def seller_motivation(listing: Listing,
                      extraction: dict | None) -> FactorResult | None:
    """Why is this being sold? — the vision's core question.

    Reads the extraction produced by atlas/scoring/motivation.py. Abstains when
    no extraction exists (no API key, an empty description, or a response that
    failed validation), which keeps a missing LLM from quietly scoring every
    listing as unmotivated.
    """
    if not extraction or extraction.get("status") != "ok":
        return None

    return FactorResult(
        value=_clamp(float(extraction.get("score", 0.0))),
        evidence={
            "kind": "llm_extraction",
            "note": (
                "seller/broker claim inferred from listing text — NOT "
                "verified. Treat as a question to ask, not a fact."
            ),
            "model": extraction.get("model"),
            "prompt_version": extraction.get("prompt_version"),
            "motivated": extraction.get("motivated"),
            "signals": extraction.get("signals", []),
            "quote": extraction.get("quote"),
            "confidence": extraction.get("confidence"),
        },
    )
