"""Deal Score v1 — transparent weighted ranking with stored evidence.

The spine collects; this judges. A score is one number 0-100 per listing, and
it is never allowed to be a black box: every factor stores the rows it read, so
"why is this 78?" always has an answer you can audit and argue with.

Three rules hold the layer together:

- **Versioned weights.** WEIGHTS lives in code and is mirrored into
  `score_weights`; changing it without bumping the version raises. Every stored
  score names the weights that produced it (plan §7).
- **Abstention, not zero.** A factor with no data for a listing returns None.
  It is still written as a `score_factors` row with `no_data` evidence, and
  `overall` is renormalised over the weight that was actually covered — so a
  thin locality lowers `coverage`, never the score. Scoring 0 for "unknown"
  would silently punish listings for Atlas's own gaps.
- **Claims are not facts.** Anything read out of listing text — legal claims,
  seller motivation — carries that in its evidence, exactly as
  atlas/ingest/legal.py does. Only the RERA registry join is a verified fact.
"""
from atlas.scoring.engine import ScoreRunResult, ScoredListing, score_listings
from atlas.scoring.weights import WEIGHTS, WEIGHTS_VERSION, ensure_weights

__all__ = [
    "WEIGHTS",
    "WEIGHTS_VERSION",
    "ScoreRunResult",
    "ScoredListing",
    "ensure_weights",
    "score_listings",
]
