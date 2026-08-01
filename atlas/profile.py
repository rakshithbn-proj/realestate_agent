"""Investor profile — the capital-aware half of Phase-2 ranking.

atlas_roadmap.md §4.5: ranking must be capital-aware, because a generic score
that surfaces Rs 5Cr villas is noise. This module encodes the user's stated
constraints and turns them into the one question the briefing must answer
before anything else: *can this actually be bought?*

Two things here are deliberately more conservative than a naive reading:

1. **Purchase costs come out of your own funds, not the loan.** A bank lends
   against the property price; Karnataka stamp duty + registration (~6.6% on a
   Rs 45L+ property) is cash on top. Ignoring that overstates buying power by
   roughly 20% — at Rs 25L own funds it is the difference between a Rs 83L
   ceiling and a Rs 68L one. Financing realism is roadmap §4.7.

2. **Legal status gates financeability.** B-khata, E-khata and revenue sites
   are largely un-financeable in Bangalore — banks decline or lend far less.
   For those the ticket ceiling collapses to *cash*, which is a different
   order of magnitude. Affordability and the legal tag are coupled, not
   independent factors, so `max_price_for()` takes the khata status.

Versioned because it judges (CLAUDE.md): bump PROFILE_VERSION on any change,
the same discipline as PARSER_VERSION. Deal Score *weights* will live in the
versioned scoring tables when they land; this is the profile they score
against.
"""
from dataclasses import dataclass, field, replace

from atlas.config import get_settings

PROFILE_VERSION = "profile-v1"

# Karnataka acquisition costs, paid in cash at registration. Stamp duty is
# slab-based on consideration value; cess (10% of duty) and surcharge apply on
# top, then 1% registration. These are the rates to cite, and they change --
# treat as config to verify, never as a settled fact (handoff §8).
REGISTRATION_RATE = 0.01
_STAMP_DUTY_SLABS = (
    # (upper bound inclusive, base stamp duty rate)
    (2_000_000, 0.02),      # up to Rs 20L
    (4_500_000, 0.03),      # Rs 20L - 45L
    (float("inf"), 0.05),   # above Rs 45L
)
_CESS_AND_SURCHARGE = 0.13   # ~10% cess + ~3% surcharge, applied to the duty

# Legal-tag items whose 'flag' status means a bank will generally not lend, so
# the purchase is cash-only. These are the real item names written by
# atlas/ingest/legal.py — a B-khata claim under 'khata_type', or a revenue-site
# claim under 'layout_approval'.
#
# Only an explicit 'flag' implies un-financeable. 'unknown' means the listing
# text made no claim, which is the common case and must NOT be read as bad
# news: treating silence as un-financeable would collapse the ceiling to cash
# for almost every listing and empty the briefing. 'jurisdiction' is
# deliberately excluded — a panchayat listing is a resale/approval risk worth
# flagging, but it is not automatically un-financeable.
UNFINANCEABLE_WHEN_FLAGGED = ("khata_type", "layout_approval")


def is_financeable(legal_tags: dict[str, str] | None) -> bool:
    """Given {tag item: status} for a listing, can it be financed at all?"""
    if not legal_tags:
        return True
    return not any(legal_tags.get(item) == "flag"
                   for item in UNFINANCEABLE_WHEN_FLAGGED)


def acquisition_cost_rate(price_inr: float) -> float:
    """Stamp duty (with cess + surcharge) plus registration, as a fraction of
    price. ~6.65% above Rs 45L."""
    for upper, rate in _STAMP_DUTY_SLABS:
        if price_inr <= upper:
            return rate * (1 + _CESS_AND_SURCHARGE) + REGISTRATION_RATE
    raise AssertionError("unreachable: slabs end at inf")


@dataclass(frozen=True)
class InvestorProfile:
    version: str = PROFILE_VERSION

    # Own funds actually deployable (not the ticket size).
    capital_min_inr: int = 1_500_000       # Rs 15L
    capital_max_inr: int = 2_500_000       # Rs 25L
    ltv: float = 0.70                      # loan-to-value where financing works

    # Markets, in priority order. Bangalore-first with Mysore data-ready is
    # handoff §4a; Mysore is active here because the user asked for it.
    cities: tuple[str, ...] = ("bangalore", "mysore")

    # Target corridors. Values are matched as normalised substrings, NOT exact
    # names: the portal emits 'Sarjapur Road', 'Sarjapur' and 'Sarjapura
    # Attibele Road' for one corridor, and 'Electronic City' / 'Electronics
    # City Phase 1' / 'Electronic City Phase 2' for another. Exact matching
    # would drop most of the inventory it is supposed to select.
    corridors: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        "south_east": (
            "sarjapur", "sarjapura", "attibele", "chandapura",
            "electronic city", "electronics city", "bommasandra",
            "hosa road", "hosur road", "kudlu",
        ),
        "north": (
            "devanahalli", "yelahanka", "hennur", "thanisandra", "jakkur",
            "bagalur", "bagaluru", "hebbal", "international airport",
        ),
        "east": (
            "whitefield", "varthur", "budigere", "hoskote", "kr puram",
            "k r puram", "kadugodi", "gunjur", "panathur", "marathahalli",
        ),
        # Mysore is one market, not corridor-segmented yet: the appreciation
        # drivers (Bangalore-Mysore Expressway, ring road) are Phase-4 config
        # per handoff §4a, and there is not enough volume to segment usefully.
        "mysore": (),
    })

    # Asset types to rank. NOTE: as of profile-v1 the MagicBricks collector
    # returns no land at all (521/521 Bangalore listings are apartment /
    # builder-floor / penthouse), so 'plot' selects nothing today. It stays
    # declared because the intent is real and the gap is a sourcing problem,
    # not a preference: closing it needs a plot-capable source (handoff §7
    # flags the paid 99acres actor as the candidate).
    property_types: tuple[str, ...] = ("plot", "apartment", "builder-floor")

    def __post_init__(self) -> None:
        # Fail loudly on nonsense config. A swapped min/max or an LTV typed as
        # 70 instead of 0.70 would not crash — it would quietly compute a
        # ceiling that is wrong by an order of magnitude, and every ranked
        # listing after it would inherit that error silently.
        if self.capital_min_inr > self.capital_max_inr:
            raise ValueError(
                f"capital_min_inr ({self.capital_min_inr:,}) exceeds "
                f"capital_max_inr ({self.capital_max_inr:,})")
        if self.capital_min_inr <= 0:
            raise ValueError("capital_min_inr must be positive")
        if not 0.0 <= self.ltv < 1.0:
            raise ValueError(
                f"ltv must be a fraction in [0, 1), got {self.ltv} "
                "(70% is 0.70, not 70)")

    def capital_for(self, optimistic: bool = False) -> int:
        return self.capital_max_inr if optimistic else self.capital_min_inr

    def max_price_for(self, financeable: bool = True,
                      optimistic: bool = True) -> int:
        """Highest purchase price the profile can actually fund.

        Solves price P from: own_funds >= down_payment + acquisition costs,
        i.e. P * (1 - ltv + cost_rate) <= capital, with ltv forced to 0 when
        the legal tags make the property un-financeable.
        """
        capital = self.capital_for(optimistic)
        ltv = self.ltv if financeable else 0.0
        # cost_rate depends on P and P depends on cost_rate. Rather than
        # iterating to a fixed point (which can oscillate across a slab
        # boundary), solve exactly: try each slab's rate and keep the answer
        # that actually falls inside that slab. Slabs ascend, so the last
        # consistent solution is the true ceiling.
        best = 0.0
        lower = 0.0
        for upper, rate in _STAMP_DUTY_SLABS:
            effective = rate * (1 + _CESS_AND_SURCHARGE) + REGISTRATION_RATE
            price = capital / (1 - ltv + effective)
            if lower < price <= upper:
                best = price
            lower = upper
        if not best:
            # Only reachable if the slabs left a gap; fall back to the top rate
            # rather than silently returning 0 (which would hide every listing).
            top_rate = _STAMP_DUTY_SLABS[-1][1] * (1 + _CESS_AND_SURCHARGE) + REGISTRATION_RATE
            best = capital / (1 - ltv + top_rate)
        return int(best)

    def cash_needed(self, price_inr: float, financeable: bool = True) -> int:
        """Total cash to close: down payment + stamp duty + registration."""
        ltv = self.ltv if financeable else 0.0
        return int(price_inr * (1 - ltv) + price_inr * acquisition_cost_rate(price_inr))

    def is_affordable(self, price_inr: float | None,
                      financeable: bool = True,
                      optimistic: bool = True) -> bool:
        """A listing with no price is not affordable-by-default — 'price on
        request' must not sneak past a capital filter."""
        if price_inr is None:
            return False
        return self.cash_needed(price_inr, financeable) <= self.capital_for(optimistic)

    def corridor_for(self, city: str, locality: str | None) -> str | None:
        """Which target corridor a locality belongs to, or None if off-target.

        Mysore has no corridor segmentation yet, so any Mysore locality (even a
        null one) is in-market.
        """
        if city not in self.cities:
            return None
        if city == "mysore":
            return "mysore"
        if not locality:
            return None
        needle = " ".join(locality.lower().split())
        for corridor, keys in self.corridors.items():
            if any(key in needle for key in keys):
                return corridor
        return None

    def is_target_locality(self, city: str, locality: str | None) -> bool:
        return self.corridor_for(city, locality) is not None


def default_profile() -> InvestorProfile:
    """The active profile, with capital and LTV read from settings.

    Capital is env-overridable on purpose: it changes as you save and deploy,
    and a stale figure mis-filters the briefing in both directions. The daily
    briefing should always print the capital it assumed, so a stale value is
    visible every morning rather than silently wrong.
    """
    settings = get_settings()
    return InvestorProfile(
        capital_min_inr=settings.atlas_capital_min_inr,
        capital_max_inr=settings.atlas_capital_max_inr,
        ltv=settings.atlas_ltv,
    )


def profile_with(**overrides) -> InvestorProfile:
    """A variant for what-if analysis (e.g. a larger capital band) without
    mutating the shared default."""
    return replace(default_profile(), **overrides)
