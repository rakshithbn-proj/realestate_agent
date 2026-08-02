"""The versioned judgement: how a deal gets ranked.

Weights live here in code and are mirrored into the `score_weights` table.
`ensure_weights()` refuses to let a stored version drift from the code — change
how deals rank and you must bump the version, the same discipline as
PARSER_VERSION and PROFILE_VERSION. Without that, yesterday's stored scores
would silently mean something different from today's.

Version 1 is **legal-first**, chosen with the user on 2026-08-02 over a
deal-hunting and a reachability-first alternative. The reasoning:

- `legal_risk` is heaviest because atlas_roadmap Appendix A puts
  legal-catastrophe avoidance at the top: a B-khata site or a rajakaluve buffer
  encroachment is not a bad deal, it is a total loss, and no discount
  compensates for it.
- `capital_fit` is second because Phase 2b's whole point is that ranking
  properties you cannot fund trains you to ignore the briefing.
- `price_vs_locality` sits below both because it is a *stand-in* for the
  guidance-value gap, not the real thing (see factors.py).
- `distress` is deliberately modest: with collection having started
  2026-08-01, price-movement history is days deep, so a heavier weight would
  mostly amplify noise.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import ScoreWeightSet

WEIGHTS_VERSION = 1

WEIGHTS: dict[str, int] = {
    "legal_risk": 30,
    "capital_fit": 25,
    "price_vs_locality": 15,
    "thesis_fit": 12,
    "distress": 10,
    "seller_motivation": 8,
    # --- declared, deliberately unweighted: no data exists yet ---
    "guidance_value_gap": 0,
    "infra_proximity": 0,
    "rental_yield": 0,
}

# Why each zero-weight factor is zero. These are written as score_factors rows
# on every score so the gap is visible in the decomposition and in the daily
# briefing, rather than quietly absent. atlas_roadmap §4.8 calls the
# guidance-value gap the core arbitrage signal — pretending it is present would
# be the single most misleading thing this module could do.
NO_DATA_FACTORS: dict[str, str] = {
    "guidance_value_gap": (
        "guidance values were never built (handoff §9.8); no guidance_values "
        "table and no collector exists. price_vs_locality is a peer-relative "
        "stand-in, NOT the statutory-vs-market arbitrage this factor means."
    ),
    "infra_proximity": (
        "needs PostGIS plus metro/PRR/STRR/airport datasets with "
        "time-to-completion decay — atlas_roadmap Phase 4."
    ),
    "rental_yield": (
        "no rental listings are collected; every source runs searchMode=buy. "
        "Appreciation, not yield, is the thesis (handoff §8) so this is low "
        "priority, but it is absent rather than judged."
    ),
}

WEIGHTS_NOTE = (
    "v1 legal-first. Chosen 2026-08-02 over deal-hunting and "
    "reachability-first alternatives. legal_risk leads because a legal "
    "catastrophe is a total loss, not a bad price; capital_fit second because "
    "an unfundable recommendation trains the reader to ignore the briefing "
    "(roadmap Phase 2b). guidance_value_gap / infra_proximity / rental_yield "
    "are declared at 0 because their data does not exist."
)

# The weights that actually move a score. Anything at 0 is declared-but-absent.
ACTIVE_FACTORS: tuple[str, ...] = tuple(f for f, w in WEIGHTS.items() if w > 0)

assert sum(WEIGHTS.values()) == 100, (
    f"WEIGHTS must sum to 100, got {sum(WEIGHTS.values())} — otherwise "
    "`overall` is not on the 0-100 scale the schema documents"
)
assert not (set(NO_DATA_FACTORS) & set(ACTIVE_FACTORS)), (
    "a factor cannot be both weighted and declared no-data"
)


class WeightsDriftError(RuntimeError):
    """Stored weights for this version differ from the code.

    Raised instead of silently overwriting: the stored scores computed under
    the old numbers would become unattributable, which is exactly what
    versioning exists to prevent. Bump WEIGHTS_VERSION instead.
    """


def ensure_weights(session: Session, note: str | None = None) -> ScoreWeightSet:
    """Return the `score_weights` row for WEIGHTS_VERSION, creating it once.

    Idempotent, and loud on drift.
    """
    row = session.get(ScoreWeightSet, WEIGHTS_VERSION)
    if row is None:
        row = ScoreWeightSet(
            version=WEIGHTS_VERSION,
            weights=dict(WEIGHTS),
            note=note or WEIGHTS_NOTE,
        )
        session.add(row)
        session.flush()
        return row

    # jsonb round-trips keys as strings; compare on a normalised dict so an
    # int/str mismatch is never mistaken for a real change.
    stored = {str(k): int(v) for k, v in (row.weights or {}).items()}
    current = {str(k): int(v) for k, v in WEIGHTS.items()}
    if stored != current:
        added = sorted(set(current) - set(stored))
        removed = sorted(set(stored) - set(current))
        changed = sorted(
            f"{k}: {stored[k]} -> {current[k]}"
            for k in set(stored) & set(current)
            if stored[k] != current[k]
        )
        raise WeightsDriftError(
            f"score_weights v{WEIGHTS_VERSION} in the database does not match "
            f"atlas/scoring/weights.py. changed={changed} added={added} "
            f"removed={removed}. Bump WEIGHTS_VERSION — editing weights in "
            "place would make every stored score unattributable."
        )
    return row
