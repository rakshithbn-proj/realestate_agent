"""Investor profile: capital-aware affordability and corridor matching.

These pin the two rules that are easy to get quietly wrong — purchase costs
coming out of own funds rather than the loan, and legal status collapsing the
ticket to cash.
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


def test_ceiling_accounts_for_costs_not_just_the_loan():
    """The naive answer at Rs 25L / 70% LTV is Rs 83L. It is wrong: stamp duty
    and registration are cash on top of the down payment."""
    p = default_profile()
    naive = p.capital_max_inr / (1 - p.ltv)          # 83.3L
    real = p.max_price_for(optimistic=True)
    assert real < naive
    assert 6_500_000 < real < 7_000_000              # ~Rs 68L


def test_ceiling_at_the_low_end_of_the_capital_band():
    # ~Rs 43.6L: lands in the 3% stamp slab (below Rs 45L), not the 5% one,
    # which is why it is not simply the Rs 68L ceiling scaled by 15/25.
    p = default_profile()
    assert 4_300_000 < p.max_price_for(optimistic=False) < 4_400_000


def test_ceiling_is_self_consistent_with_its_own_stamp_slab():
    """The solved price must sit in the slab whose rate produced it —
    otherwise the ceiling is computed with the wrong duty."""
    from atlas.profile import acquisition_cost_rate as rate
    p = default_profile()
    for optimistic in (True, False):
        ceiling = p.max_price_for(optimistic=optimistic)
        implied = p.capital_for(optimistic) / (1 - p.ltv + rate(ceiling))
        assert abs(implied - ceiling) <= 1


def test_unfinanceable_status_collapses_the_ceiling_to_cash():
    """B-khata / revenue sites are largely un-financeable — the ticket is cash,
    an order of magnitude below the financed ceiling."""
    p = default_profile()
    financed = p.max_price_for(financeable=True, optimistic=True)
    cash_only = p.max_price_for(financeable=False, optimistic=True)
    assert cash_only < financed / 2
    assert 2_200_000 < cash_only < 2_500_000         # ~Rs 23.5L


def test_financeability_reads_the_real_legal_tag_vocabulary():
    """Statuses must match what atlas/ingest/legal.py actually writes:
    items khata_type / layout_approval, statuses pass|flag|unknown."""
    assert is_financeable({"khata_type": "flag"}) is False      # B-khata
    assert is_financeable({"layout_approval": "flag"}) is False  # revenue site
    assert is_financeable({"khata_type": "pass"}) is True
    # Silence is the common case and must not be read as bad news
    assert is_financeable({"khata_type": "unknown"}) is True
    assert is_financeable({}) is True
    assert is_financeable(None) is True
    # A panchayat jurisdiction is a risk, but not automatically un-financeable
    assert is_financeable({"jurisdiction": "flag"}) is True


def test_cash_needed_includes_down_payment_and_duty():
    p = default_profile()
    price = 6_000_000
    cash = p.cash_needed(price, financeable=True)
    assert cash == pytest.approx(price * 0.30 + price * 0.0665, rel=1e-3)
    # Un-financeable: the whole price plus duty
    assert p.cash_needed(price, financeable=False) > price


def test_affordability_respects_legal_status():
    p = default_profile()
    price = 6_000_000                                # Rs 60L
    assert p.is_affordable(price, financeable=True) is True
    assert p.is_affordable(price, financeable=False) is False


def test_missing_price_is_never_affordable():
    """'Price on request' must not slip past a capital filter."""
    assert default_profile().is_affordable(None) is False


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


def test_capital_comes_from_settings_not_hardcoded(monkeypatch):
    """Regression: .env.example advertised ATLAS_CAPITAL_* overrides that
    nothing read, so setting them on the VPS silently changed nothing and the
    briefing kept filtering against the wrong capital."""
    from types import SimpleNamespace

    import atlas.profile as profile_mod

    monkeypatch.setattr(profile_mod, "get_settings", lambda: SimpleNamespace(
        atlas_capital_min_inr=2_000_000,
        atlas_capital_max_inr=2_000_000,
        atlas_ltv=0.60))
    p = profile_mod.default_profile()
    assert p.capital_max_inr == 2_000_000
    assert p.ltv == 0.60
    # Compare against an explicit baseline, NOT default_profile() — that reads
    # the same patched settings, so it would compare a value to itself.
    baseline = profile_mod.InvestorProfile(
        capital_min_inr=1_500_000, capital_max_inr=2_500_000, ltv=0.70)
    assert p.max_price_for() < baseline.max_price_for()


def test_ceiling_falling_in_a_stamp_slab_gap_errs_conservative():
    """Rs 20L at 60% LTV lands in a genuine gap: the 3% slab solves to
    Rs 45.06L (just above its own Rs 45L ceiling) while the 5% slab solves to
    Rs 42.87L (below where 5% starts). Neither is self-consistent, so the
    fallback applies the HIGHER duty — understating buying power rather than
    overstating it. Wrong in the safe direction."""
    p = profile_with(capital_min_inr=2_000_000, capital_max_inr=2_000_000, ltv=0.60)
    ceiling = p.max_price_for()
    assert 4_200_000 < ceiling < 4_350_000
    assert p.cash_needed(ceiling) <= p.capital_max_inr


def test_nonsense_capital_config_fails_loudly():
    """A swapped band or an LTV typed as 70 must raise, not quietly compute a
    ceiling that is wrong by an order of magnitude."""
    with pytest.raises(ValueError, match="exceeds"):
        profile_with(capital_min_inr=5_000_000, capital_max_inr=2_000_000)
    with pytest.raises(ValueError, match="0.70, not 70"):
        profile_with(ltv=70)
    with pytest.raises(ValueError, match="positive"):
        profile_with(capital_min_inr=0, capital_max_inr=0)


def test_profile_is_versioned_and_overridable():
    p = default_profile()
    assert p.version == PROFILE_VERSION
    bigger = profile_with(capital_max_inr=10_000_000)
    assert bigger.max_price_for() > p.max_price_for()
    assert p.capital_max_inr == 2_500_000        # default not mutated
