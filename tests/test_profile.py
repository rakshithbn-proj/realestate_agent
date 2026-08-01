"""Investor profile: reserve-first capital, affordability, corridor matching.

These pin the rules that are easy to get quietly wrong — purchase costs coming
out of own funds rather than the loan, the emergency fund never counting as
buyable, and legal status collapsing the ticket to cash.
"""
import pytest

from atlas.profile import (
    PROFILE_VERSION,
    acquisition_cost_rate,
    default_profile,
    is_financeable,
    profile_with,
)


def test_acquisition_costs_are_slabbed():
    # Above Rs 45L: 5% duty + 13% cess/surcharge on the duty + 1% registration
    assert acquisition_cost_rate(6_000_000) == pytest.approx(0.0665, abs=1e-4)
    # Rs 20L-45L slab is 3% base
    assert acquisition_cost_rate(3_000_000) == pytest.approx(0.0439, abs=1e-4)
    assert acquisition_cost_rate(1_000_000) < acquisition_cost_rate(3_000_000)


# --- capital: reserve-first -------------------------------------------------

def test_reserve_is_never_deployable():
    """The emergency fund is not buying power. Spending it is how an owner
    becomes a forced seller — the distress this system looks for in others."""
    p = default_profile()
    assert p.liquid_total_inr == 2_500_000
    assert p.reserved_inr == 600_000
    assert p.deployable_inr == 1_900_000
    # The ceiling is computed off deployable, never off the full portfolio
    assert p.max_price_for() < profile_with(reserved_inr=0).max_price_for()


def test_ceiling_accounts_for_costs_not_just_the_loan():
    """The naive answer at Rs 19L / 70% LTV is Rs 63.3L. It is wrong: stamp
    duty and registration are cash on top of the down payment."""
    p = default_profile()
    naive = p.deployable_inr / (1 - p.ltv)           # 63.3L
    real = p.max_price_for()
    assert real < naive
    assert 5_100_000 < real < 5_300_000              # ~Rs 51.8L


def test_ceiling_is_self_consistent_with_its_own_stamp_slab():
    """The solved price must sit in the slab whose rate produced it —
    otherwise the ceiling is computed with the wrong duty."""
    p = profile_with(monthly_contribution_inr=50_000)
    for months in (0, 12):
        ceiling = p.max_price_for(months=months)
        implied = p.capital_for(months) / (1 - p.ltv + acquisition_cost_rate(ceiling))
        assert abs(implied - ceiling) <= 1


def test_unfinanceable_status_collapses_the_ceiling_to_cash():
    """B-khata / revenue sites are largely un-financeable — the ticket is cash,
    a different order of magnitude from the financed ceiling."""
    p = default_profile()
    financed = p.max_price_for(financeable=True)
    cash_only = p.max_price_for(financeable=False)
    assert cash_only < financed / 2
    assert 1_750_000 < cash_only < 1_900_000         # ~Rs 18.4L


def test_cash_needed_includes_down_payment_and_duty():
    p = default_profile()
    price = 6_000_000
    cash = p.cash_needed(price, financeable=True)
    assert cash == pytest.approx(price * 0.30 + price * 0.0665, rel=1e-3)
    # Un-financeable: the whole price plus duty
    assert p.cash_needed(price, financeable=False) > price


def test_affordability_respects_legal_status():
    p = default_profile()
    price = 5_000_000                                # Rs 50L
    assert p.is_affordable(price, financeable=True) is True
    assert p.is_affordable(price, financeable=False) is False


def test_missing_price_is_never_affordable():
    """'Price on request' must not slip past a capital filter."""
    assert default_profile().is_affordable(None) is False


# --- the runway: what to do when you cannot buy yet -------------------------

def test_contributions_extend_buying_power_over_time():
    p = profile_with(monthly_contribution_inr=50_000)
    assert p.deployable_at(0) == 1_900_000
    assert p.deployable_at(12) == 2_500_000
    assert p.max_price_for(months=12) > p.max_price_for(months=0)


def test_deployable_at_ignores_investment_growth():
    """Money needed inside ~3 years should not sit in equity, so the runway
    must not assume returns on it — that would overstate the date AND
    encourage the drawdown risk that can kill a purchase before registration."""
    p = profile_with(monthly_contribution_inr=10_000)
    assert p.deployable_at(24) == 1_900_000 + 240_000      # exactly, no compounding


def test_months_until_affordable_counts_the_wait():
    p = profile_with(monthly_contribution_inr=50_000)
    price = 5_500_000
    assert p.is_affordable(price) is False                 # not yet
    months = p.months_until_affordable(price)
    assert months == 3
    assert p.is_affordable(price, months=months) is True
    assert p.is_affordable(price, months=months - 1) is False


def test_already_affordable_is_zero_months():
    assert default_profile().months_until_affordable(4_000_000) == 0


def test_never_affordable_when_prices_outrun_savings():
    """The decision-relevant answer. If the corridor appreciates faster than
    you accumulate, waiting moves the target away — the honest advice is buy
    smaller or further out NOW, not 'keep saving'."""
    p = profile_with(monthly_contribution_inr=5_000)
    assert p.months_until_affordable(5_500_000, annual_appreciation=0.30) is None
    # Same property, same savings, flat market -> reachable
    assert p.months_until_affordable(5_500_000, annual_appreciation=0.0) is not None


def test_no_contribution_and_unaffordable_is_never():
    p = profile_with(monthly_contribution_inr=0)
    assert p.months_until_affordable(20_000_000) is None


def test_emi_raises_the_reserve_requirement():
    """Taking a secured loan changes the downside from 'tight months' to
    losing the asset, so the reserve must also cover the EMI."""
    p = profile_with(reserved_inr=100_000)
    assert p.reserve_shortfall_for_emi(30_000) == 80_000     # needs 6 x 30k
    # A reserve that already covers six months of EMI is fine
    assert default_profile().reserve_shortfall_for_emi(30_000) == 0


# --- legal / corridors ------------------------------------------------------

def test_financeability_reads_the_real_legal_tag_vocabulary():
    """Statuses must match what atlas/ingest/legal.py actually writes:
    items khata_type / layout_approval, statuses pass|flag|unknown."""
    assert is_financeable({"khata_type": "flag"}) is False       # B-khata
    assert is_financeable({"layout_approval": "flag"}) is False   # revenue site
    assert is_financeable({"khata_type": "pass"}) is True
    # Silence is the common case and must not be read as bad news
    assert is_financeable({"khata_type": "unknown"}) is True
    assert is_financeable({}) is True
    assert is_financeable(None) is True
    # A panchayat jurisdiction is a risk, but not automatically un-financeable
    assert is_financeable({"jurisdiction": "flag"}) is True


def test_corridor_matching_handles_portal_name_fragmentation():
    """Real locality strings from the ingested data — one corridor arrives
    under several spellings, so exact matching would drop most of it."""
    p = default_profile()
    for name in ("Sarjapur Road", "Sarjapur", "Sarjapura Attibele Road",
                 "Electronic City", "Electronics City Phase 1",
                 "Electronic City Phase 2", "Hosa Road"):
        assert p.corridor_for("bangalore", name) == "south_east", name
    for name in ("Yelahanka", "Yelahanka New Town", "Thanisandra",
                 "Thanisandra Main Road", "Hennur Main Road", "Devanahalli",
                 "Bagalur Main Road", "Bagaluru"):
        assert p.corridor_for("bangalore", name) == "north", name
    for name in ("Whitefield", "Varthur", "Hoskote", "KR Puram", "Kadugodi"):
        assert p.corridor_for("bangalore", name) == "east", name


def test_off_target_localities_are_rejected():
    p = default_profile()
    for name in ("Jayanagar", "Rajaji Nagar", "Mysore Road", "Mission Road"):
        assert p.corridor_for("bangalore", name) is None, name
    assert p.is_target_locality("bangalore", None) is False


def test_mysore_is_in_market_without_corridor_segmentation():
    p = default_profile()
    assert p.corridor_for("mysore", "Hunsur Road") == "mysore"
    assert p.corridor_for("mysore", None) == "mysore"


def test_unknown_city_is_off_target():
    assert default_profile().corridor_for("chennai", "Anywhere") is None


# --- config wiring ----------------------------------------------------------

def test_capital_comes_from_settings_not_hardcoded(monkeypatch):
    """Regression: .env.example advertised ATLAS_* capital overrides that
    nothing read, so setting them on the VPS silently changed nothing and the
    briefing kept filtering against the wrong capital."""
    from types import SimpleNamespace

    import atlas.profile as profile_mod

    monkeypatch.setattr(profile_mod, "get_settings", lambda: SimpleNamespace(
        atlas_liquid_total_inr=2_600_000,
        atlas_reserved_inr=600_000,
        atlas_monthly_contribution_inr=40_000,
        atlas_ltv=0.60))
    p = profile_mod.default_profile()
    assert p.deployable_inr == 2_000_000
    assert p.monthly_contribution_inr == 40_000
    assert p.ltv == 0.60
    # Compare against an explicit baseline, NOT default_profile() — that reads
    # the same patched settings, so it would compare a value to itself.
    baseline = profile_mod.InvestorProfile(
        liquid_total_inr=2_500_000, reserved_inr=600_000, ltv=0.70)
    assert p.max_price_for() < baseline.max_price_for()


def test_ceiling_falling_in_a_stamp_slab_gap_errs_conservative():
    """Rs 20L at 60% LTV lands in a genuine gap: the 3% slab solves to
    Rs 45.06L (just above its own Rs 45L ceiling) while the 5% slab solves to
    Rs 42.87L (below where 5% starts). Neither is self-consistent, so the
    fallback applies the HIGHER duty — understating buying power rather than
    overstating it. Wrong in the safe direction."""
    p = profile_with(liquid_total_inr=2_600_000, reserved_inr=600_000, ltv=0.60)
    ceiling = p.max_price_for()
    assert 4_200_000 < ceiling < 4_350_000
    assert p.cash_needed(ceiling) <= p.deployable_inr


def test_nonsense_capital_config_fails_loudly():
    """A reserve bigger than the portfolio, or an LTV typed as 70, must raise
    rather than quietly compute a ceiling wrong by an order of magnitude."""
    with pytest.raises(ValueError, match="exceeds"):
        profile_with(liquid_total_inr=1_000_000, reserved_inr=2_000_000)
    with pytest.raises(ValueError, match="0.70, not 70"):
        profile_with(ltv=70)
    with pytest.raises(ValueError, match="negative"):
        profile_with(liquid_total_inr=-1, reserved_inr=0)


def test_profile_is_versioned_and_overridable():
    p = default_profile()
    assert p.version == PROFILE_VERSION == "profile-v2"
    bigger = profile_with(liquid_total_inr=10_000_000)
    assert bigger.max_price_for() > p.max_price_for()
    assert p.liquid_total_inr == 2_500_000        # default not mutated
