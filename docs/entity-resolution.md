# Entity Resolution Design (Module M2)

> The hardest and most valuable module: one physical property, many listings across portals. This spec defines the matching pipeline, thresholds, and evaluation plan referenced by [plan.md](../plan.md) §5.

## Problem

The same Bangalore flat appears on MagicBricks at ₹95L (broker A), 99acres at ₹92L (broker B), and NoBroker at ₹89L (owner). Without resolution the price tracker sees three properties; with it, one property with a ₹6L cross-portal spread — which is itself a negotiation signal. Errors in both directions are costly: false merges corrupt price history silently; false splits hide the spread signal.

**Bias: precision over recall.** A missed merge loses one signal; a wrong merge poisons the data under it. Start conservative and loosen with evidence.

## Pipeline

```
listings ──► 1. normalize ──► 2. block ──► 3. score pairs ──► 4. decide ──► properties
                                                                  │
                                                    review queue ◄┘ (ambiguous band)
```

### Stage 1 — Normalization (deterministic, stored on the listing row)

| Field | Normalization |
|---|---|
| Address | Lowercase, strip punctuation; expand Bangalore aliases (`whitefield` = `whitefield, bangalore east`; `hsr` = `hsr layout`; maintain an alias table seeded manually, grown over time). Split into components: project, street, locality, city. |
| Project name | Strip builder prefixes/suffixes (`Prestige`, `Phase 2`, `Apartments`, `Residency` noise words), collapse whitespace. Keep both raw and normalized. |
| Locality | Map to a canonical `localities` row (seeded from a Bangalore locality list; fuzzy-assign at ingest, flag unknowns). |
| GPS | Store as-is + geohash-6 (~±600m). Portal GPS is often locality-centroid, so it's a blocking key, not proof. |
| Area | Convert to sqft (from sqm, guntas, acres, cents). Store original + converted. |
| Price | Convert to INR integer (from `95L`, `1.2Cr`, `1,20,00,000`). Guard against magnitude typos (see plan §7 data quality). |
| BHK / floor / facing | Parse to integers/enums; null when absent — never guess. |

**LLM use in Stage 1 only:** Haiku (batched, cheap) parses free-text addresses/descriptions into the structured components above when regex parsing fails. The LLM never makes match decisions — extraction only, so every decision below stays deterministic and auditable.

### Stage 2 — Blocking (candidate generation)

Never compare all pairs. A listing's candidates are the union of:

1. Same normalized project name (exact match on normalized form)
2. Same geohash-6 cell (or neighbor cell) AND same BHK AND same property type
3. Same phone number of lister (strong key when present)

Expected candidate set per listing: single digits. Everything not blocked together is assumed distinct.

### Stage 3 — Pairwise scoring

Weighted feature sum → score 0–100. Initial weights (tune against labeled data, version in `score_weights`):

| Feature | Signal | Weight | Notes |
|---|---|---|---|
| Project name similarity | token-set ratio ≥ 0.9 | 25 | RapidFuzz `token_set_ratio` on normalized names |
| Area match | within ±3% | 20 | Same flat listed by two brokers rarely differs more |
| Floor match | exact | 10 | Nulls contribute 0, not negative |
| GPS distance | < 250 m | 10 | Low weight — centroid problem |
| Price proximity | within ±15% | 10 | Wider band: the spread is the signal we want to keep |
| Image overlap | ≥ 1 pHash pair with Hamming ≤ 8 | 20 | Perceptual hash of listing photos; strongest cross-portal evidence when present |
| BHK + type match | exact | 5 | Mostly enforced by blocking already |
| Same-lister penalty | same phone, same portal | −15 | Brokers relist the same ad; dedupe *within* portal separately first |

### Stage 4 — Decision thresholds

**Strong-match rule (checked first, deterministic):** exact normalized project name AND area within ±3% AND floor exact match AND distinct listers ⇒ auto-merge, regardless of weighted score. This exists because many listings lack GPS and photos — without this rule their maximum weighted score is 70, below the auto-merge bar, and the review queue would flood with obvious duplicates.

Otherwise, by weighted score:

| Score | Action |
|---|---|
| ≥ 80 | Auto-merge into a `properties` entity |
| 55–79 | Review queue — surfaced in the daily digest ("possible duplicate, confirm?"), one-tap confirm/reject |
| < 55 | Distinct |

**Review-queue volume guard:** in the first two weeks, measure the score-band distribution. If the review band exceeds ~10 pairs/day, tighten blocking or promote more strong-match rules before loosening thresholds — the queue must stay small enough that reviewing it is a 2-minute daily habit, not a chore.

Merges are **reversible by construction**: `listings.property_id` is a soft link, and every merge/split writes an `entity_merge_log` row with the score, feature breakdown, and actor (auto/human). Un-merging is an update + log entry, never data loss.

### Incremental operation

Resolution runs per-ingest on new/changed listings only (candidates come from blocking keys, so it's an index lookup, not a scan). A nightly job re-scores review-queue pairs whose underlying listings changed.

## Evaluation

- **Labeled set:** hand-label ~100 candidate pairs (stratified across score bands) once two portals are live. This is a few hours of work and is the ground truth for all tuning.
- **Targets:** ≥95% precision on auto-merges (the plan's ≥90% is the floor); recall is reported but not gated.
- **Regression:** the labeled set runs in CI whenever weights or normalizers change; weight changes bump `score_weights.version` so historical merges remain attributable.
- **Drift check:** monthly, sample 10 random auto-merges for manual spot-check; one bad merge triggers a threshold review.

## Failure modes and mitigations

| Failure | Mitigation |
|---|---|
| Portal GPS = locality centroid → false geo matches | Low GPS weight; require corroborating name/area/image evidence |
| New launches: hundreds of identical units in one project | Area+floor+price identical by design — require floor match AND distinct-lister check; if still ambiguous, leave split (units in the same project are near-substitutes anyway, so the cost of a false split is low here) |
| Broker copies another broker's photos | Image overlap alone never auto-merges (max 20 < 80 threshold) |
| Locality alias table gaps | Unknown localities flagged at ingest; weekly review adds aliases |
| Same broker relists with tweaked price | Within-portal dedupe pass (same lister + same project + area) runs before cross-portal resolution |
