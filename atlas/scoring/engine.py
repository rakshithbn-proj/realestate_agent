"""Compute and store Deal Scores.

The whole point of this module is the two rules in the package docstring:
`overall` is renormalised over the weight that actually had data, and every
factor — including the ones that had none — is written as a `score_factors`
row with its evidence. A score you cannot interrogate is a number, not a
judgement.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Numeric, cast, delete, func, select
from sqlalchemy.orm import Session

from atlas.config import get_settings
from atlas.ingest.legal import legal_tags_by_listing
from atlas.models import Listing, Locality, PriceEvent, Score, ScoreFactor
from atlas.profile import InvestorProfile, default_profile
from atlas.scoring import factors as F
from atlas.scoring import motivation
from atlas.scoring.weights import (
    ACTIVE_FACTORS,
    NO_DATA_FACTORS,
    WEIGHTS,
    WEIGHTS_VERSION,
    ensure_weights,
)

log = logging.getLogger(__name__)

# Listings whose Rs/sqft feeds a locality median. Removed stock is excluded:
# a median that includes withdrawn listings measures history, not the market
# you can buy in today.
_LIVE = ("active", "relisted")


@dataclass
class FactorRow:
    factor: str
    value: float | None          # None = abstained
    weight: int
    evidence: dict

    @property
    def contribution(self) -> float:
        return 0.0 if self.value is None else self.value * self.weight


@dataclass
class ScoredListing:
    listing_id: int
    overall: float               # 0-100
    coverage: float              # share of non-zero weight that had data
    factors: list[FactorRow] = field(default_factory=list)

    def explain(self) -> list[FactorRow]:
        """Weighted factors, strongest contribution first."""
        return sorted(
            (f for f in self.factors if f.weight > 0),
            key=lambda f: f.contribution,
            reverse=True,
        )


@dataclass
class ScoreRunResult:
    weights_version: int
    score_date: str
    scored: int
    skipped: int
    written: bool                # False on a dry run
    results: list[ScoredListing] = field(default_factory=list)


def _ist_today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def locality_medians(
    session: Session,
) -> dict[tuple[str | None, int | None, str], tuple[float, int]]:
    """Median Rs/sqft per (city, locality, asset_class), with sample counts.

    Segmented by asset class because land and built stock are not comparable
    on a per-sqft basis — see factors.asset_class. Done in one pass in
    Postgres rather than per listing in Python: ~650 listings today, but this
    is the query that grows with the archive.
    """
    rows = session.execute(
        select(
            Listing.city,
            Listing.locality_id,
            Listing.property_type,
            Listing.price_per_sqft,
        ).where(
            Listing.status.in_(_LIVE),
            Listing.price_per_sqft.isnot(None),
        )
    ).all()

    buckets: dict[tuple[str | None, int | None, str], list[float]] = {}
    for city, locality_id, property_type, ppsf in rows:
        cls = F.asset_class(property_type)
        if cls is None or ppsf is None:
            continue
        buckets.setdefault((city, locality_id, cls), []).append(float(ppsf))

    medians = {}
    for key, values in buckets.items():
        values.sort()
        n = len(values)
        mid = n // 2
        median = values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2
        medians[key] = (median, n)
    return medians


def _price_events(session: Session) -> dict[int, list[PriceEvent]]:
    events: dict[int, list[PriceEvent]] = {}
    for event in session.scalars(
        select(PriceEvent).order_by(PriceEvent.listing_id, PriceEvent.observed_at)
    ):
        events.setdefault(event.listing_id, []).append(event)
    return events


def score_listing(
    listing: Listing,
    *,
    profile: InvestorProfile,
    tags: dict[str, str],
    tag_details: dict[str, dict],
    medians: dict,
    events: list[PriceEvent],
    locality_name: str | None,
    extraction: dict | None,
    now: datetime | None = None,
) -> ScoredListing:
    """Pure: everything it needs is passed in, so it is trivially testable and
    has no idea a database exists."""
    computed = {
        "legal_risk": F.legal_risk(listing, tags, tag_details),
        "capital_fit": F.capital_fit(listing, tags, profile),
        "price_vs_locality": F.price_vs_locality(listing, medians),
        "thesis_fit": F.thesis_fit(listing, profile, locality_name),
        "distress": F.distress(listing, events, now=now),
        "seller_motivation": F.seller_motivation(listing, extraction),
    }

    rows: list[FactorRow] = []
    for name in ACTIVE_FACTORS:
        result = computed.get(name)
        if result is None:
            # Plain abstention: the factor had nothing to add beyond "no data".
            rows.append(FactorRow(
                factor=name, value=None, weight=WEIGHTS[name],
                evidence={"kind": "abstained",
                          "reason": "no data for this listing"},
            ))
        else:
            # An explained abstention keeps its evidence (value stays None),
            # so the decomposition says WHY, not just that it was missing.
            rows.append(FactorRow(factor=name, value=result.value,
                                  weight=WEIGHTS[name],
                                  evidence=result.evidence))

    # The declared-but-dataless factors. Written at weight 0 so the gap is
    # visible in the decomposition and in the briefing, never silently absent.
    for name, reason in NO_DATA_FACTORS.items():
        rows.append(FactorRow(
            factor=name, value=None, weight=0,
            evidence={"kind": "no_data", "reason": reason},
        ))

    covered = sum(r.weight for r in rows if r.value is not None)
    total = sum(r.contribution for r in rows)
    # Renormalise over covered weight: a listing whose locality is too thin to
    # compare scores on what IS known, rather than being pushed down for a gap
    # that is Atlas's, not the property's.
    overall = (total / covered * 100.0) if covered else 0.0
    active_weight = sum(WEIGHTS[f] for f in ACTIVE_FACTORS)
    coverage = covered / active_weight if active_weight else 0.0

    return ScoredListing(listing_id=listing.id, overall=round(overall, 2),
                         coverage=round(coverage, 4), factors=rows)


def score_listings(
    session: Session,
    profile: InvestorProfile | None = None,
    listing_ids: list[int] | None = None,
    dry_run: bool = False,
) -> ScoreRunResult:
    """Score every live listing (or the given ids) and store the result.

    Idempotent within an Asia/Kolkata day: re-running replaces today's row and
    its factors. Yesterday's is immutable, so a recommendation already emailed
    still points at the number that was actually sent.
    """
    profile = profile or default_profile()
    if not dry_run:
        ensure_weights(session)

    query = select(Listing).where(Listing.status.in_(_LIVE))
    if listing_ids is not None:
        query = query.where(Listing.id.in_(listing_ids))
    listings = list(session.scalars(query))

    tags_by_listing = legal_tags_by_listing(session)
    details_by_listing = legal_tags_by_listing(session, with_detail=True)
    medians = locality_medians(session)
    events = _price_events(session)
    localities = {loc.id: loc.name for loc in session.scalars(select(Locality))}
    extractions = motivation.motivations_for(
        session, [listing.id for listing in listings]
    )

    today = _ist_today()
    now = datetime.now(timezone.utc)
    results: list[ScoredListing] = []
    skipped = 0

    for listing in listings:
        scored = score_listing(
            listing,
            profile=profile,
            tags=tags_by_listing.get(listing.id, {}),
            tag_details=details_by_listing.get(listing.id, {}),
            medians=medians,
            events=events.get(listing.id, []),
            locality_name=localities.get(listing.locality_id),
            extraction=extractions.get(listing.id),
            now=now,
        )
        if scored.coverage == 0.0:
            # Nothing was known about this listing at all — an untagged,
            # unpriced stub. Storing a 0 would rank it as a bad deal instead
            # of an unexamined one.
            skipped += 1
            continue
        results.append(scored)
        if not dry_run:
            _persist(session, scored, today)

    if not dry_run:
        session.commit()

    results.sort(key=lambda s: s.overall, reverse=True)
    return ScoreRunResult(
        weights_version=WEIGHTS_VERSION,
        score_date=today.isoformat(),
        scored=len(results),
        skipped=skipped,
        written=not dry_run,
        results=results,
    )


def _persist(session: Session, scored: ScoredListing, score_date: date) -> None:
    row = session.scalar(
        select(Score).where(
            Score.listing_id == scored.listing_id,
            Score.weights_version == WEIGHTS_VERSION,
            Score.score_date == score_date,
        )
    )
    if row is None:
        row = Score(
            listing_id=scored.listing_id,
            weights_version=WEIGHTS_VERSION,
            score_date=score_date,
            overall=scored.overall,
            coverage=scored.coverage,
        )
        session.add(row)
        session.flush()
    else:
        row.overall = scored.overall
        row.coverage = scored.coverage
        row.computed_at = datetime.now(timezone.utc)
        # Replace the decomposition wholesale: a factor that abstained today
        # but not yesterday must not leave yesterday's evidence behind.
        session.execute(
            delete(ScoreFactor).where(ScoreFactor.score_id == row.id)
        )

    for factor in scored.factors:
        session.add(ScoreFactor(
            score_id=row.id,
            factor=factor.factor,
            # NOT NULL in the schema; an abstention is recorded as 0 with
            # evidence saying so, never as a computed zero.
            value=factor.value if factor.value is not None else 0.0,
            evidence=factor.evidence,
        ))


def latest_scores(session: Session, limit: int = 20, city: str | None = None,
                  reachable_only: bool = True,
                  profile: InvestorProfile | None = None) -> list[dict]:
    """Top listings by most recent score, with their decomposition.

    `reachable_only` is the Phase-2b rule in query form: the briefing must
    never surface a property that cannot be funded on the day it is shown.
    """
    profile = profile or default_profile()
    newest = (
        select(Score.listing_id,
               func.max(Score.score_date).label("score_date"))
        .where(Score.listing_id.isnot(None))
        .group_by(Score.listing_id)
        .subquery()
    )
    query = (
        select(Score, Listing)
        .join(newest, (Score.listing_id == newest.c.listing_id)
              & (Score.score_date == newest.c.score_date))
        .join(Listing, Listing.id == Score.listing_id)
        .where(Listing.status.in_(_LIVE))
        .order_by(cast(Score.overall, Numeric).desc())
    )
    if city:
        query = query.where(Listing.city == city)

    tags_by_listing = legal_tags_by_listing(session)
    localities = {loc.id: loc.name for loc in session.scalars(select(Locality))}

    out: list[dict] = []
    for score, listing in session.execute(query):
        tags = tags_by_listing.get(listing.id, {})
        financeable = F.is_financeable(tags)
        affordable = profile.is_affordable(
            float(listing.price_inr) if listing.price_inr else None, financeable)
        if reachable_only and not affordable:
            continue
        factor_rows = session.scalars(
            select(ScoreFactor).where(ScoreFactor.score_id == score.id)
        ).all()
        out.append({
            "listing_id": listing.id,
            "overall": float(score.overall),
            "coverage": float(score.coverage),
            "weights_version": score.weights_version,
            "score_date": score.score_date.isoformat(),
            "city": listing.city,
            "locality": localities.get(listing.locality_id),
            "property_type": listing.property_type,
            "price_inr": listing.price_inr,
            "area_sqft": float(listing.area_sqft) if listing.area_sqft else None,
            "url": listing.url,
            "financeable": financeable,
            "affordable_now": affordable,
            "cash_needed_inr": (
                profile.cash_needed(float(listing.price_inr), financeable)
                if listing.price_inr else None),
            "factors": [
                {"factor": f.factor, "value": float(f.value),
                 "weight": WEIGHTS.get(f.factor, 0), "evidence": f.evidence}
                for f in sorted(factor_rows,
                                key=lambda f: WEIGHTS.get(f.factor, 0),
                                reverse=True)
            ],
        })
        if len(out) >= limit:
            break
    return out
