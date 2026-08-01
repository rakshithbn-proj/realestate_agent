"""Capital Plan: the runway to a first purchase.

The plan is what the briefing shows before you can transact, so its failure
modes are specific: showing a target you cannot reach, hiding that the market
is outrunning your savings, or forgetting that liquidating investments costs
tax that comes out of the same pot.
"""
from pathlib import Path

import pytest
from sqlalchemy import select

from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import Listing
from atlas.plan import build_plan, format_plan
from atlas.profile import default_profile, profile_with

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"


def _spec(city: str = "bangalore") -> SourceSpec:
    return SourceSpec(name="magicbricks", city=city, kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(FIXTURE)})


def _locality(session, city, name):
    from atlas.models import Locality
    loc = session.scalar(select(Locality).where(
        Locality.city == city, Locality.name == name))
    if loc is None:
        loc = Locality(city=city, name=name)
        session.add(loc)
        session.flush()
    return loc


def _seed(session, city="bangalore", locality="Sarjapur Road",
          price=4_000_000, n=1):
    """Put exactly n priced, in-corridor listings in the DB.

    The fixture ships 15 listings with their own localities and prices, several
    of which land in target corridors. Park them all off-corridor and unpriced
    first, then promote exactly n — otherwise the assertions are really testing
    the fixture's contents rather than the plan.
    """
    run_source(session, _spec(city))
    rows = session.scalars(select(Listing)).all()
    off = _locality(session, city, "Jayanagar")        # deliberately off-target
    for r in rows:
        r.locality_id = off.id
        r.price_inr = None
        r.city = city
    target = _locality(session, city, locality)
    for r in rows[:n]:
        r.locality_id = target.id
        r.price_inr = price
    session.commit()
    return rows[:n]


# --- the ladder -------------------------------------------------------------

def test_plan_uses_real_listings_not_a_guessed_price(session):
    _seed(session, price=4_000_000)
    plan = build_plan(session, profile_with(monthly_contribution_inr=50_000))
    assert plan.rungs
    assert all(r.price_inr == 4_000_000 for r in plan.rungs)
    assert plan.considered > 0


def test_unpriced_listings_are_excluded(session):
    """'Price on request' cannot be planned against — including it would
    produce a bar of zero and a countdown of 'now'."""
    rows = _seed(session, price=4_000_000, n=2)
    rows[0].price_inr = None
    session.commit()
    plan = build_plan(session)
    assert all(r.price_inr is not None for r in plan.rungs)


def test_off_corridor_listings_are_not_rungs(session):
    _seed(session, locality="Jayanagar", price=4_000_000)
    plan = build_plan(session)
    assert plan.rungs == []
    assert "Nothing to plan against yet" in format_plan(plan)


def test_ladder_is_ordered_by_cash_bar_not_price(session):
    """The ladder starts at the lowest real bar. Cash needed is what gates
    you, and an un-financeable cheaper property can need MORE cash."""
    _seed(session, price=4_000_000, n=3)
    rows = session.scalars(select(Listing)).all()
    rows[0].price_inr = 3_000_000
    rows[1].price_inr = 5_000_000
    session.commit()
    plan = build_plan(session)
    bars = [r.cash_needed_inr for r in plan.rungs]
    assert bars == sorted(bars)


def test_months_away_reflects_the_savings_rate(session):
    # Rs 80L is beyond the default deployable, so there is a runway to measure
    _seed(session, price=8_000_000)
    slow = build_plan(session, profile_with(monthly_contribution_inr=20_000))
    fast = build_plan(session, profile_with(monthly_contribution_inr=100_000))
    assert slow.rungs[0].months_away > fast.rungs[0].months_away > 0


def test_market_running_faster_than_savings_is_surfaced(session):
    """The decision-relevant case: reachable on a flat market, never if the
    corridor keeps appreciating. Hiding this would mean advising 'keep
    saving' toward a price that is receding."""
    _seed(session, price=8_000_000)
    plan = build_plan(session, profile_with(monthly_contribution_inr=5_000),
                      market_appreciation=0.30)
    rung = plan.rungs[0]
    assert rung.months_away is not None          # reachable if prices hold
    assert rung.months_away_if_market_runs is None   # not if they run
    assert "never if mkt" in format_plan(plan)


# --- unlocking committed capital -------------------------------------------

def test_unlock_covers_both_the_gap_and_its_own_tax(session):
    """The tax is paid from the same pot. Ignoring it is how a plan comes up
    short at the registrar's office."""
    p = profile_with(liquid_total_inr=1_000_000, reserved_inr=600_000,
                     committed_inr=1_000_000, committed_gain_fraction=1.0)
    price = 3_000_000
    gap = p.unlock_needed_for(price)
    assert gap > 0
    tax = p.unlock_cost(gap)
    assert tax > 0
    # Reachable only because gap + tax still fits inside committed holdings
    assert gap + tax <= p.committed_inr
    assert p.can_unlock_for(price) is True


def test_unlock_refused_when_committed_cannot_cover_gap_plus_tax():
    p = profile_with(liquid_total_inr=700_000, reserved_inr=600_000,
                     committed_inr=200_000, committed_gain_fraction=1.0)
    assert p.can_unlock_for(9_000_000) is False


def test_no_unlock_needed_when_already_affordable():
    p = default_profile()
    assert p.unlock_needed_for(3_000_000) == 0
    assert p.can_unlock_for(3_000_000) is True
    assert p.unlock_cost(0) == 0


def test_ltcg_exemption_applies():
    """Small liquidations are untaxed up to the annual exemption."""
    p = profile_with(committed_inr=1_000_000, committed_gain_fraction=0.10)
    assert p.unlock_cost(1_000_000) == 0        # 1L gain, under exemption
    bigger = profile_with(committed_inr=2_000_000, committed_gain_fraction=0.50)
    assert bigger.unlock_cost(2_000_000) > 0


def test_unlock_cost_never_exceeds_committed_holdings():
    """Asking to unlock more than exists must not invent tax."""
    p = profile_with(committed_inr=500_000, committed_gain_fraction=1.0)
    assert p.unlock_cost(10_000_000) == p.unlock_cost(500_000)


def test_plan_reports_unlock_ceiling_above_cash_ceiling(session):
    _seed(session, price=4_000_000)
    plan = build_plan(session, profile_with(committed_inr=2_000_000))
    assert plan.ceiling_with_unlock_inr > plan.ceiling_now_inr
    assert plan.committed_inr == 2_000_000


def test_format_is_ascii_only(session):
    """The plan goes into logs and email; non-ASCII gets mangled by the
    Windows console codepage (an em dash arrived as 'u')."""
    _seed(session, price=4_000_000)
    text = format_plan(build_plan(session))
    assert text.isascii()
    assert "cannot be borrowed" in text
