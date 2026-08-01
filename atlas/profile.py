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

PROFILE_VERSION = "profile-v2"

# Karnataka acquisition costs, paid in cash at registration. Stamp duty is
# slab-based on consideration value; cess (10% of duty) and surcharge apply on
# top, then 1% registration. These are the rates to cite, and they change --
# treat as config to verify, never as a settled fact (handoff §8).
REGISTRATION_RATE = 0.01

# Equity LTCG, for costing a liquidation that funds a purchase. Approximate and
# subject to change — a decision aid, not a filing figure.
LTCG_RATE = 0.125
LTCG_EXEMPTION_INR = 125_000
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

    # --- Capital, modelled reserve-first (profile-v2) ---
    # v1 held a single capital band, which silently treated the emergency fund
    # as buyable. It is not: spending it is how an owner becomes a forced
    # seller, which is precisely the distress this system is built to find in
    # OTHER people. Reserve comes off the top, always.
    liquid_total_inr: int = 2_500_000       # MFs + stocks + cash
    reserved_inr: int = 600_000             # emergency fund — never deployable
    monthly_contribution_inr: int = 0       # added toward the goal each month
    ltv: float = 0.70                       # where financing is available

    # Long-term holdings that are NEITHER emergency reserve NOR earmarked for
    # this goal — equity you would not normally break into. Tracked separately
    # rather than lumped into `reserved` because the honest answer to "should I
    # break into my investments for this one deal?" is sometimes yes, and it
    # needs a number: what it unlocks, and what the tax costs.
    committed_inr: int = 0
    # Share of committed value that is unrealised gain, for the LTCG estimate.
    committed_gain_fraction: float = 0.35

    # Months of EMI the reserve should ALSO cover once a loan exists. Taking on
    # a secured EMI raises the emergency-fund requirement: the downside stops
    # being "tight months" and becomes losing the asset. Used by
    # `reserve_shortfall_for_emi` to warn before, not after, committing.
    emi_months_covered: int = 6

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
        # Fail loudly on nonsense config. A reserve larger than the portfolio,
        # or an LTV typed as 70 instead of 0.70, would not crash — it would
        # quietly compute a ceiling wrong by an order of magnitude, and every
        # ranked listing after it would inherit that error silently.
        # Negatives first: otherwise liquid_total=-1 trips the "exceeds" branch
        # and reports a confusing comparison instead of the real mistake.
        if self.liquid_total_inr < 0 or self.reserved_inr < 0:
            raise ValueError("capital figures must not be negative")
        if self.monthly_contribution_inr < 0:
            raise ValueError("monthly_contribution_inr must not be negative")
        if self.reserved_inr > self.liquid_total_inr:
            raise ValueError(
                f"reserved_inr ({self.reserved_inr:,}) exceeds "
                f"liquid_total_inr ({self.liquid_total_inr:,})")
        if not 0.0 <= self.ltv < 1.0:
            raise ValueError(
                f"ltv must be a fraction in [0, 1), got {self.ltv} "
                "(70% is 0.70, not 70)")

    @property
    def deployable_inr(self) -> int:
        """Cash actually available for a purchase, after the reserve."""
        return max(0, self.liquid_total_inr - self.reserved_inr)

    def deployable_at(self, months: int) -> int:
        """Deployable cash after `months` of contributions.

        Deliberately ignores investment growth on the earmarked money: cash
        needed inside ~3 years should not be sitting in equity, so assuming
        returns on it would both overstate the runway and encourage exactly
        the drawdown risk that can kill a purchase weeks before registration.
        """
        if months < 0:
            raise ValueError("months must not be negative")
        return self.deployable_inr + self.monthly_contribution_inr * months

    def capital_for(self, months: int = 0) -> int:
        return self.deployable_at(months)

    def unlock_cost(self, amount_inr: float) -> int:
        """Tax cost of liquidating `amount_inr` of committed long-term holdings.

        Approximate: equity LTCG at 12.5% above the annual exemption, on the
        unrealised-gain share of what is sold. Rates change and your actual
        basis differs per holding — treat this as a decision aid to check with
        a CA, never as a filing figure.
        """
        amount = max(0.0, min(float(amount_inr), self.committed_inr))
        gain = amount * self.committed_gain_fraction
        taxable = max(0.0, gain - LTCG_EXEMPTION_INR)
        return int(taxable * LTCG_RATE)

    def unlock_needed_for(self, price_inr: float, financeable: bool = True,
                          months: int = 0) -> int:
        """Committed capital that must be liquidated to close this purchase,
        after deployable cash. 0 when no unlock is required."""
        gap = self.cash_needed(price_inr, financeable) - self.deployable_at(months)
        return max(0, int(gap))

    def can_unlock_for(self, price_inr: float, financeable: bool = True,
                       months: int = 0) -> bool:
        """Is the purchase reachable at all, counting committed holdings?

        The tax is paid from the same pot, so the unlock has to cover both the
        gap and its own cost — ignoring that is how a plan comes up short at
        the registrar's office.
        """
        gap = self.unlock_needed_for(price_inr, financeable, months)
        if gap == 0:
            return True
        return gap + self.unlock_cost(gap) <= self.committed_inr

    def reserve_shortfall_for_emi(self, monthly_emi_inr: float) -> int:
        """How much the emergency fund is short once this EMI exists.

        Taking on a secured loan raises the reserve requirement — the reserve
        must now also cover `emi_months_covered` of repayments. Returns 0 when
        the current reserve already covers it.
        """
        needed = monthly_emi_inr * self.emi_months_covered
        return max(0, int(needed - self.reserved_inr))

    def max_price_for(self, financeable: bool = True,
                      months: int = 0) -> int:
        """Highest purchase price the profile can fund `months` from now.

        Solves price P from: deployable >= down_payment + acquisition costs,
        i.e. P * (1 - ltv + cost_rate) <= deployable, with ltv forced to 0 when
        the legal tags make the property un-financeable.
        """
        capital = self.capital_for(months)
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
                      months: int = 0) -> bool:
        """A listing with no price is not affordable-by-default — 'price on
        request' must not sneak past a capital filter."""
        if price_inr is None:
            return False
        return self.cash_needed(price_inr, financeable) <= self.capital_for(months)

    def months_until_affordable(self, price_inr: float,
                                financeable: bool = True,
                                annual_appreciation: float = 0.0,
                                horizon_months: int = 240) -> int | None:
        """Months until this becomes affordable, or None if never.

        `None` is the decision-relevant answer, not an error: when the market
        appreciates faster than you accumulate, waiting moves the target away
        from you and the honest advice is to buy smaller or further out NOW
        rather than save toward a price that keeps receding. A briefing that
        only ever said "keep saving" would hide that.
        """
        for m in range(0, horizon_months + 1):
            grown = price_inr * (1 + annual_appreciation) ** (m / 12)
            if self.cash_needed(grown, financeable) <= self.deployable_at(m):
                return m
        return None

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
        liquid_total_inr=settings.atlas_liquid_total_inr,
        reserved_inr=settings.atlas_reserved_inr,
        monthly_contribution_inr=settings.atlas_monthly_contribution_inr,
        ltv=settings.atlas_ltv,
        committed_inr=settings.atlas_committed_inr,
        committed_gain_fraction=settings.atlas_committed_gain_fraction,
    )


def profile_with(**overrides) -> InvestorProfile:
    """A variant for what-if analysis (e.g. a larger capital band) without
    mutating the shared default."""
    return replace(default_profile(), **overrides)
