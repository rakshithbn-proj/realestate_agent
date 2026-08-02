"""Deal Score v1.

The failure modes worth guarding are all about *dishonesty in the number*:
scoring a listing on data Atlas never had, letting a locality with three
promoted comps look like a market, comparing a plot's Rs/sqft against
apartments, or quietly forgetting that a legal flag also collapses the ticket
to cash. Each test below pins one of those.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from atlas.ingest.legal import tag_listings
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import (
    Listing,
    ListingLegalTag,
    ListingMotivation,
    Locality,
    PriceEvent,
    Score,
    ScoreFactor,
    ScoreWeightSet,
)
from atlas.profile import profile_with
from atlas.scoring import factors as F
from atlas.scoring import motivation
from atlas.scoring.engine import latest_scores, locality_medians, score_listings
from atlas.scoring.report import format_explain, format_score_run, format_top
from atlas.scoring.weights import (
    ACTIVE_FACTORS,
    NO_DATA_FACTORS,
    WEIGHTS,
    WEIGHTS_VERSION,
    WeightsDriftError,
    ensure_weights,
)

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"


def _spec(city: str = "bangalore") -> SourceSpec:
    return SourceSpec(name="magicbricks", city=city, kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(FIXTURE)})


def _locality(session, city, name):
    loc = session.scalar(select(Locality).where(
        Locality.city == city, Locality.name == name))
    if loc is None:
        loc = Locality(city=city, name=name)
        session.add(loc)
        session.flush()
    return loc


def _listing(session, city="bangalore", locality="Sarjapur Road",
             price=3_000_000, area=1200.0, property_type="apartment",
             lister_kind="broker", title="A listing", description=None,
             external_id=None, posted_at=None):
    """One listing, built directly rather than through the pipeline, so each
    test controls exactly the fields it is about."""
    loc = _locality(session, city, locality) if locality else None
    n = session.scalar(select(Listing.id).order_by(Listing.id.desc())) or 0
    source_id = session.scalar(select(Listing.source_id).limit(1))
    if source_id is None:
        from atlas.models import Source
        src = Source(name="test", city=city, kind="portal", fetcher="fixture")
        session.add(src)
        session.flush()
        source_id = src.id
    listing = Listing(
        source_id=source_id,
        external_id=external_id or f"ext-{n + 1}",
        status="active",
        title=title,
        description=description,
        city=city,
        locality_id=loc.id if loc else None,
        property_type=property_type,
        area_sqft=area,
        price_inr=price,
        lister_kind=lister_kind,
        posted_at=posted_at,
        parser_version="test/1.0.0",
        rera_ids=[],
    )
    session.add(listing)
    session.flush()
    return listing


def _tag(session, listing, item, status, detail=None):
    session.add(ListingLegalTag(listing_id=listing.id, item=item, status=status,
                                detail=detail, evidence={"kind": "test"},
                                tagger_version="test/1.0.0"))
    session.flush()


def _tag_all(session, listing, rera="pass", khata="unknown",
             jurisdiction="unknown", layout="unknown"):
    _tag(session, listing, "rera_registered", rera)
    _tag(session, listing, "khata_type", khata)
    _tag(session, listing, "jurisdiction", jurisdiction)
    _tag(session, listing, "layout_approval", layout)


def _factor(scored, name):
    return next(f for f in scored.factors if f.factor == name)


# --- the versioned judgement ------------------------------------------------

def test_weights_sum_to_100_and_declare_their_gaps():
    assert sum(WEIGHTS.values()) == 100
    assert set(NO_DATA_FACTORS) <= set(WEIGHTS)
    assert all(WEIGHTS[name] == 0 for name in NO_DATA_FACTORS)
    # guidance_value_gap is the roadmap's stated core arbitrage signal and was
    # never built. It must stay visibly declared, not quietly dropped.
    assert "guidance_value_gap" in NO_DATA_FACTORS


def test_weights_row_is_written_once_and_is_idempotent(session):
    first = ensure_weights(session)
    session.commit()
    again = ensure_weights(session)
    assert first.version == again.version == WEIGHTS_VERSION
    assert session.scalar(select(ScoreWeightSet).where(
        ScoreWeightSet.version == WEIGHTS_VERSION)).weights == WEIGHTS


def test_changing_weights_without_bumping_the_version_raises(session):
    ensure_weights(session)
    session.commit()
    row = session.get(ScoreWeightSet, WEIGHTS_VERSION)
    row.weights = {**WEIGHTS, "legal_risk": 5}
    session.flush()
    with pytest.raises(WeightsDriftError) as exc:
        ensure_weights(session)
    # The message must name what moved — a bare "drift" tells you nothing.
    assert "legal_risk" in str(exc.value)


# --- legal risk, and its coupling to affordability --------------------------

def test_legal_flag_collapses_the_score_and_forces_cash_only(session):
    """The coupling, end to end: a B-khata claim is not just a lower score, it
    also removes financing, which changes what you can afford."""
    clean = _listing(session, external_id="clean", price=3_000_000)
    flagged = _listing(session, external_id="flagged", price=3_000_000)
    _tag_all(session, clean)
    _tag_all(session, flagged, khata="flag")
    session.commit()

    profile = profile_with(liquid_total_inr=3_000_000, reserved_inr=0)
    result = score_listings(session, profile=profile)
    by_id = {s.listing_id: s for s in result.results}

    assert (_factor(by_id[flagged.id], "legal_risk").value
            < _factor(by_id[clean.id], "legal_risk").value)
    # ...and the same flag has removed the loan, so the cash requirement jumps.
    clean_cash = _factor(by_id[clean.id], "capital_fit").evidence["cash_needed_inr"]
    flagged_cash = _factor(by_id[flagged.id], "capital_fit").evidence["cash_needed_inr"]
    assert flagged_cash > clean_cash
    assert _factor(by_id[flagged.id], "capital_fit").evidence["financeable"] is False


def test_unknown_legal_tags_are_not_read_as_bad_news(session):
    """0 of 670 listings carry a khata claim today, so 'unknown' is the normal
    case. Treating it as a flag would empty the briefing."""
    listing = _listing(session)
    _tag_all(session, listing, khata="unknown", layout="unknown")
    session.commit()
    result = score_listings(session)
    scored = result.results[0]
    assert _factor(scored, "capital_fit").evidence["financeable"] is True
    assert _factor(scored, "legal_risk").value > 0.5


def test_legal_risk_abstains_on_an_untagged_listing(session):
    """Untagged is unexamined, not clean."""
    listing = _listing(session)
    session.commit()
    result = score_listings(session)
    assert _factor(result.results[0], "legal_risk").value is None


def test_legal_evidence_carries_the_not_verified_warning(session):
    listing = _listing(session)
    _tag_all(session, listing, khata="pass")
    session.commit()
    result = score_listings(session)
    note = _factor(result.results[0], "legal_risk").evidence["note"]
    assert "NOT" in note and "document-verified" in note


# --- abstention and coverage ------------------------------------------------

def test_no_data_factors_are_written_with_their_reason(session):
    listing = _listing(session)
    _tag_all(session, listing)
    session.commit()
    score_listings(session)

    score = session.scalar(select(Score))
    rows = {r.factor: r for r in session.scalars(
        select(ScoreFactor).where(ScoreFactor.score_id == score.id))}
    for name, reason in NO_DATA_FACTORS.items():
        assert rows[name].evidence["kind"] == "no_data"
        assert rows[name].evidence["reason"] == reason
        assert float(rows[name].value) == 0.0


def test_unpriced_listing_abstains_on_capital_and_is_never_affordable(session):
    """'Price on request' must not sneak past a capital filter."""
    listing = _listing(session, price=None)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session)
    scored = result.results[0]
    assert _factor(scored, "capital_fit").value is None
    assert scored.coverage < 1.0
    assert latest_scores(session, reachable_only=True) == []


def test_abstention_renormalises_rather_than_depressing_the_score(session):
    """Two identical listings, one with a factor abstaining, must not differ
    just because Atlas knows less about one of them."""
    full = _listing(session, external_id="full", price=3_000_000)
    thin = _listing(session, external_id="thin", price=3_000_000)
    _tag_all(session, full)
    session.commit()
    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    assert _factor(by_id[thin.id], "legal_risk").value is None
    assert by_id[thin.id].coverage < by_id[full.id].coverage
    # Still on the 0-100 scale despite the missing weight.
    assert 0.0 <= by_id[thin.id].overall <= 100.0


def test_listing_with_nothing_known_is_skipped_not_scored_zero(session):
    """An unpriced, untagged, typeless stub is unexamined. A 0 would rank it
    as a bad deal."""
    listing = _listing(session, price=None, property_type=None, locality=None)
    session.commit()
    result = score_listings(session)
    assert result.scored == 0
    assert result.skipped == 1
    assert session.scalar(select(Score)) is None


# --- price_vs_locality: the guidance-value stand-in --------------------------

def test_price_vs_locality_abstains_below_the_comp_minimum(session):
    """A median over three promoted listings is noise wearing a number."""
    for i in range(F.MIN_COMPS - 1):
        listing = _listing(session, external_id=f"comp-{i}", price=3_000_000)
        _tag_all(session, listing)
    session.commit()
    result = score_listings(session)
    assert all(_factor(s, "price_vs_locality").value is None
               for s in result.results)


def test_price_vs_locality_never_compares_a_plot_to_apartments(session):
    """Land and built stock are priced on different bases. Without the split, a
    plot at Rs 4,600/sqft next to flats at Rs 8,000 reads as a 45% discount and
    dominates the ranking on an artefact."""
    for i in range(6):
        flat = _listing(session, external_id=f"flat-{i}", price=9_600_000,
                        area=1200.0, property_type="apartment")
        _tag_all(session, flat)
    plot = _listing(session, external_id="plot", price=5_520_000, area=1200.0,
                    property_type="Residential Plot")
    _tag_all(session, plot)
    session.commit()

    medians = locality_medians(session)
    keys = {k[2] for k in medians}
    assert keys == {"built", "land"}

    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    # Only one land comp exists, so the plot abstains instead of being scored
    # a bargain against apartment prices.
    assert _factor(by_id[plot.id], "price_vs_locality").value is None


def test_price_vs_locality_scores_a_genuine_discount(session):
    for i in range(6):
        listing = _listing(session, external_id=f"flat-{i}", price=9_600_000,
                           area=1200.0)
        _tag_all(session, listing)
    cheap = _listing(session, external_id="cheap", price=7_200_000, area=1200.0)
    _tag_all(session, cheap)
    session.commit()
    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    factor = _factor(by_id[cheap.id], "price_vs_locality")
    assert factor.value == pytest.approx(1.0)
    assert factor.evidence["comps"] >= F.MIN_COMPS
    # The evidence must say what it is NOT, or it will be mistaken for the
    # guidance-value gap it stands in for.
    assert "guidance-value gap" in factor.evidence["note"]


# --- distress and days-on-market --------------------------------------------

def test_days_on_market_uses_posted_at_not_first_seen_at(session):
    old = datetime.now(timezone.utc) - timedelta(days=200)
    listing = _listing(session, posted_at=old)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session)
    evidence = _factor(result.results[0], "distress").evidence
    assert evidence["days_on_market_source"] == "posted_at"
    assert evidence["days_on_market"] >= 199
    assert evidence["components"]["days_on_market"] == pytest.approx(1.0)


def test_distress_abstains_entirely_with_no_posted_at_and_no_price_history(session):
    """Today's real state: collection started 2026-08-01, so most listings have
    neither. Abstaining is correct — reporting them all as un-distressed would
    be a claim Atlas cannot support."""
    listing = _listing(session, posted_at=None)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session)
    factor = _factor(result.results[0], "distress")
    assert factor.value is None
    assert "abstained" in factor.evidence["days_on_market_note"]


def test_price_drop_drives_distress_without_a_posting_date(session):
    listing = _listing(session, price=9_000_000, posted_at=None)
    _tag_all(session, listing)
    session.add(PriceEvent(listing_id=listing.id, old_price=None,
                           new_price=10_000_000))
    session.add(PriceEvent(listing_id=listing.id, old_price=10_000_000,
                           new_price=9_000_000, pct_change=-10.0))
    session.commit()
    result = score_listings(session)
    factor = _factor(result.results[0], "distress")
    assert factor.value is not None and factor.value > 0
    assert factor.evidence["drop_from_peak_pct"] == pytest.approx(10.0)
    assert factor.evidence["reductions"] == 1
    assert "days_on_market" not in factor.evidence["components"]


# --- thesis fit -------------------------------------------------------------

def test_plot_outranks_apartment_on_thesis_fit(session):
    plot = _listing(session, external_id="plot", property_type="Residential Plot")
    flat = _listing(session, external_id="flat", property_type="apartment")
    _tag_all(session, plot)
    _tag_all(session, flat)
    session.commit()
    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    assert (_factor(by_id[plot.id], "thesis_fit").value
            > _factor(by_id[flat.id], "thesis_fit").value)


def test_owner_direct_outranks_brokered(session):
    owner = _listing(session, external_id="owner", lister_kind="owner")
    broker = _listing(session, external_id="broker", lister_kind="broker")
    _tag_all(session, owner)
    _tag_all(session, broker)
    session.commit()
    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    assert (_factor(by_id[owner.id], "thesis_fit").value
            > _factor(by_id[broker.id], "thesis_fit").value)


def test_off_corridor_listing_is_ranked_down(session):
    on = _listing(session, external_id="on", locality="Sarjapur Road")
    off = _listing(session, external_id="off", locality="Jayanagar")
    _tag_all(session, on)
    _tag_all(session, off)
    session.commit()
    result = score_listings(session)
    by_id = {s.listing_id: s for s in result.results}
    assert _factor(by_id[off.id], "thesis_fit").evidence["corridor"] is None
    assert (_factor(by_id[off.id], "thesis_fit").value
            < _factor(by_id[on.id], "thesis_fit").value)


# --- seller motivation ------------------------------------------------------

def test_seller_motivation_abstains_with_no_api_key(session, monkeypatch):
    """A missing key must never look like 'no seller here is motivated'."""
    listing = _listing(session, description="Urgent sale, owner relocating")
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session)
    scored = result.results[0]
    assert _factor(scored, "seller_motivation").value is None
    assert 0.0 <= scored.overall <= 100.0


def test_seller_motivation_reads_a_cached_extraction(session):
    listing = _listing(session, description="Owner relocating abroad")
    _tag_all(session, listing)
    session.add(ListingMotivation(
        listing_id=listing.id,
        prompt_version=motivation.MOTIVATION_PROMPT_VERSION,
        model=motivation.MODEL, source_hash="abc", status="ok",
        motivated=True, score=0.7, signals=["relocation"],
        quote="Owner relocating abroad", confidence=1.0,
    ))
    session.commit()
    result = score_listings(session)
    factor = _factor(result.results[0], "seller_motivation")
    assert factor.value == pytest.approx(0.7)
    assert factor.evidence["quote"] == "Owner relocating abroad"
    assert "NOT" in factor.evidence["note"]


def test_pending_or_refused_extraction_abstains(session):
    for status in ("pending", "refused", "invalid", "empty"):
        listing = _listing(session, external_id=f"l-{status}")
        _tag_all(session, listing)
        session.add(ListingMotivation(
            listing_id=listing.id,
            prompt_version=motivation.MOTIVATION_PROMPT_VERSION,
            model=motivation.MODEL, source_hash="abc", status=status,
        ))
    session.commit()
    result = score_listings(session)
    assert all(_factor(s, "seller_motivation").value is None
               for s in result.results)


def test_motivation_score_takes_the_strongest_signal_not_the_sum():
    """Stacking weak signals must not out-rank one genuine distress signal."""
    strong = motivation.Extraction(motivated=True, signals=["distress_sale"],
                                   quote="q", confidence=1.0)
    many_weak = motivation.Extraction(
        motivated=True,
        signals=["price_negotiable", "portfolio_exit", "nri_seller"],
        quote="q", confidence=1.0)
    assert motivation.derive_score(strong) > motivation.derive_score(many_weak)
    assert motivation.derive_score(strong) == pytest.approx(1.0)


def test_motivation_score_is_zero_when_not_motivated():
    assert motivation.derive_score(motivation.Extraction(
        motivated=False, signals=[], quote="", confidence=0.9)) == 0.0


def test_unknown_signal_is_ignored_rather_than_guessed():
    """A signal outside the vocabulary means the vocabulary changed without a
    version bump. Guessing a weight would hide that."""
    assert motivation.derive_score(motivation.Extraction(
        motivated=True, signals=["vibes"], quote="q", confidence=1.0)) == 0.0


# --- persistence ------------------------------------------------------------

def test_same_day_rerun_is_idempotent(session):
    listing = _listing(session)
    _tag_all(session, listing)
    session.commit()

    score_listings(session)
    score_listings(session)

    scores = session.scalars(select(Score)).all()
    assert len(scores) == 1
    factor_rows = session.scalars(select(ScoreFactor)).all()
    assert len(factor_rows) == len(ACTIVE_FACTORS) + len(NO_DATA_FACTORS)


def test_rescoring_replaces_stale_factor_evidence(session):
    """A factor that abstains today must not leave yesterday's evidence
    attached to today's score."""
    listing = _listing(session, price=3_000_000)
    _tag_all(session, listing)
    session.commit()
    score_listings(session)

    listing.price_inr = None
    session.commit()
    score_listings(session)

    score = session.scalar(select(Score))
    row = session.scalar(select(ScoreFactor).where(
        ScoreFactor.score_id == score.id, ScoreFactor.factor == "capital_fit"))
    assert row.evidence["kind"] == "abstained"


def test_dry_run_writes_nothing(session):
    listing = _listing(session)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session, dry_run=True)
    assert result.scored == 1
    assert result.written is False
    assert session.scalar(select(Score)) is None
    assert session.scalar(select(ScoreWeightSet)) is None


def test_scores_are_bounded_and_a_cheaper_listing_ranks_at_least_as_high(session):
    dear = _listing(session, external_id="dear", price=9_000_000, area=1200.0)
    cheap = _listing(session, external_id="cheap", price=3_000_000, area=1200.0)
    _tag_all(session, dear)
    _tag_all(session, cheap)
    session.commit()
    profile = profile_with(liquid_total_inr=4_000_000, reserved_inr=0)
    result = score_listings(session, profile=profile)
    by_id = {s.listing_id: s for s in result.results}
    assert all(0.0 <= s.overall <= 100.0 for s in result.results)
    assert by_id[cheap.id].overall >= by_id[dear.id].overall


# --- the reachable-only rule (roadmap Phase 2b) -----------------------------

def test_top_excludes_what_cannot_be_funded_today(session):
    reachable = _listing(session, external_id="reachable", price=2_000_000)
    unreachable = _listing(session, external_id="unreachable", price=90_000_000)
    _tag_all(session, reachable)
    _tag_all(session, unreachable)
    session.commit()
    profile = profile_with(liquid_total_inr=3_000_000, reserved_inr=0)
    score_listings(session, profile=profile)

    ids = [r["listing_id"] for r in latest_scores(session, profile=profile)]
    assert reachable.id in ids
    assert unreachable.id not in ids
    # ...but it is still scored and watched, so a price drop into range shows up.
    all_ids = [r["listing_id"] for r in
               latest_scores(session, profile=profile, reachable_only=False)]
    assert unreachable.id in all_ids


def test_top_returns_the_full_decomposition(session):
    listing = _listing(session, price=2_000_000)
    _tag_all(session, listing)
    session.commit()
    profile = profile_with(liquid_total_inr=3_000_000, reserved_inr=0)
    score_listings(session, profile=profile)
    row = latest_scores(session, profile=profile)[0]
    names = {f["factor"] for f in row["factors"]}
    assert set(ACTIVE_FACTORS) <= names
    assert set(NO_DATA_FACTORS) <= names


# --- the CLI-facing renderers -----------------------------------------------
# These are the surfaces a human actually reads, and a crash in one breaks
# `atlas.cli score` / `top` without any other test noticing.

def test_score_run_report_names_the_gaps(session):
    listing = _listing(session)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session, dry_run=True)
    text = format_score_run(result)
    assert "DRY RUN" in text
    assert "DISTRIBUTION" in text
    assert "FACTOR COVERAGE" in text
    # The briefing must state what it could not judge.
    assert "NOT SCORED" in text
    for name in NO_DATA_FACTORS:
        assert name in text


def test_explain_renders_every_factor_with_its_evidence(session):
    listing = _listing(session, price=2_000_000)
    _tag_all(session, listing)
    session.commit()
    result = score_listings(session, listing_ids=[listing.id], dry_run=True)
    text = format_explain(session, result, listing.id)
    assert f"LISTING {listing.id}" in text
    for name in ACTIVE_FACTORS:
        assert name in text
    assert "ABSTAINED" in text          # seller_motivation, with no API key


def test_explain_is_graceful_when_the_listing_was_not_scored(session):
    result = score_listings(session, dry_run=True)
    assert "not scored" in format_explain(session, result, 999_999)


def test_top_renders_and_says_so_when_empty(session):
    assert "Nothing to show" in format_top([], reachable_only=True)
    listing = _listing(session, price=2_000_000)
    _tag_all(session, listing)
    session.commit()
    profile = profile_with(liquid_total_inr=3_000_000, reserved_inr=0)
    score_listings(session, profile=profile)
    text = format_top(latest_scores(session, profile=profile))
    assert "TOP LISTINGS" in text
    assert "legal_risk=" in text


def test_top_flags_a_cash_only_listing(session):
    listing = _listing(session, price=1_000_000)
    _tag_all(session, listing, khata="flag")
    session.commit()
    profile = profile_with(liquid_total_inr=3_000_000, reserved_inr=0)
    score_listings(session, profile=profile)
    text = format_top(latest_scores(session, profile=profile))
    assert "cash only" in text


# --- the real fixture, end to end -------------------------------------------

def test_scores_real_fixture_listings_end_to_end(session):
    run_source(session, _spec())
    tag_listings(session)
    result = score_listings(session)
    assert result.scored > 0
    assert all(0.0 <= s.overall <= 100.0 for s in result.results)
    # posted_at came through the parser, so days-on-market is real from day one.
    assert session.scalar(
        select(Listing).where(Listing.posted_at.isnot(None))) is not None
