# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Atlas: a personal AI system for Karnataka real-estate investing (Bangalore first, Mysore data-ready). The planning docs are authoritative and layered — read [handoff.md](handoff.md) first; precedence for *plans* is atlas_roadmap.md → overall_plan.md → handoff.md, and handoff.md wins for *current facts*. plan.md holds the reliability engineering (§7), cost model (§8), and risk register (§9). `trial/` is a retired spike — reference only, never extend it (`trial/sources/rera.py` gets ported into `atlas/ingest/` in Phase 1).

## Commands

```sh
.venv\Scripts\python -m pytest                       # full suite (~2.5 min; boots throwaway Postgres)
.venv\Scripts\python -m pytest tests\test_pipeline.py -k idempotent   # single test
.venv\Scripts\python -m tests.regen_golden           # regenerate parser golden file — review the diff
.venv\Scripts\alembic upgrade head                   # apply migrations (DATABASE_URL from .env)
scripts\dev.ps1                                       # local app: portable PG + migrate + uvicorn --reload
docker compose up -d --build                         # VPS deploy only (dev box has no Docker)
```

Manual source runs (idempotent — same code as the scheduler):

```sh
.venv\Scripts\python -m atlas.cli run rera                  # RERA registry (free, no token)
.venv\Scripts\python -m atlas.cli run magicbricks           # Bangalore portal (needs APIFY_TOKEN)
.venv\Scripts\python -m atlas.cli run magicbricks_mysore    # Mysore portal
.venv\Scripts\python -m atlas.cli daily                     # full sequence; exits non-zero on a bad day
.venv\Scripts\python -m atlas.cli sweep-and-tag             # staleness sweep + legal tagging
.venv\Scripts\python -m atlas.cli health                    # per-source health JSON
.venv\Scripts\python -m atlas.cli gate                      # Phase-1 gate: consecutive clean days
.venv\Scripts\python -m atlas.cli plan                      # capital plan: cash bar + countdown
.venv\Scripts\python -m atlas.cli score --dry-run           # score distribution, writes nothing
.venv\Scripts\python -m atlas.cli score --explain <id>      # one listing's full decomposition
.venv\Scripts\python -m atlas.cli top --limit 15 [--all]    # ranked; --all includes unfundable
.venv\Scripts\python -m atlas.cli digest --dry-run          # render the briefing, send nothing
.venv\Scripts\python -m atlas.cli reparse --source <name>   # replay raw_payloads through the parser
```

`run <source>` choices come from the registry, so a new `SourceSpec` is
runnable immediately. On the VPS the same commands run as
`docker compose exec atlas-app python -m atlas.cli <cmd>`.

- Setup: `python -m venv .venv` then `.venv\Scripts\pip install -e .[dev]`.
- **The dev machine (Windows) has no Docker, no WSL, no installed Postgres.** E2E tests get Postgres via the fallback chain in [tests/conftest.py](tests/conftest.py): `ATLAS_TEST_DATABASE_URL` env → `pgserver` (Linux/macOS only; no Windows wheels) → portable binaries in `.pgbin/pgsql/bin` (gitignored, already extracted on this machine; see [tests/_local_pg.py](tests/_local_pg.py)). Windows gotcha: never run `pg_ctl start` with captured pipes — the postmaster inherits them and the wait hangs forever.
- CI (`.github/workflows/ci.yml`) runs the same suite against a `pgvector/pgvector:pg16` service container.

## Architecture

**Ingestion is raw-first** (the load-bearing design rule): `run_source()` in [atlas/ingest/pipeline.py](atlas/ingest/pipeline.py) opens a `scrape_runs` row → fetcher ([atlas/ingest/fetchers/](atlas/ingest/fetchers/)) returns raw items → **every raw item is archived to `raw_payloads` before parsing** → parser ([atlas/ingest/parsers/](atlas/ingest/parsers/)) normalizes → upsert into `listings` keyed on `(source_id, external_id)`, with immutable `listing_versions` snapshots (`new`/`updated`/`price_changed`) and `price_events`. Re-runs are idempotent. Sources are declared as `SourceSpec` in [atlas/ingest/registry.py](atlas/ingest/registry.py); tests point a spec's fixture fetcher at a saved actor payload.

**DDL is owned by Alembic migrations** ([alembic/versions/](alembic/versions/)), written as raw SQL. [docs/schema.sql](docs/schema.sql) is the *design* source of truth and must be kept in sync with migrations by hand — reviews audit one against the other. [atlas/models.py](atlas/models.py) maps *only* the ingestion-spine tables the code touches (ORM never emits DDL); unmapped tables get ORM classes when their module lands.

**Schema decisions that constrain future work:**
- Embeddings are deferred: no vector columns / pgvector extension exist yet. They land in the Phase-3 semantic-search migration pinned to whichever embedding model is chosen then (assumed voyage-3.5, 1024-dim). Until then the schema must stay runnable on stock Postgres 16 (`pg_trgm` only) — local tests depend on this.
- Multi-city from day one: `city` market slug (`'bangalore'`/`'mysore'`) on `sources`, `localities`, `listings`; uniqueness is city-scoped. Never assume single-market.
- `listings.rera_ids text[]` holds canonicalized `PRM/KA/RERA/...` ids (portal prefixes like `TOR/` stripped) — this is the ~99.6% join to the RERA registry.

**Two ingestion entry points, both raw-first:** portals go through the generic
`run_source()` (registry `SourceSpec` → fetcher → parser → pipeline); RERA has
its own `atlas/ingest/rera.py` (single GET renders the whole registry; four
positional JS arrays; `parse()` asserts equal lengths and fails loudly). Both
share `_trailing_avg_items` and the anomaly thresholds. Daily orchestration is
`atlas/jobs.py` (RERA → portals → sweep+tag), exposed via `atlas/cli.py` and an
optional in-process APScheduler (`ATLAS_ENABLE_SCHEDULER=1`, jobs pinned to
Asia/Kolkata).

**Removal is inferred from sustained absence, never from one run.** A ~300-item
actor sample is not a full snapshot, so `sweep_stale_listings` only marks
listings removed when the source has an *`ok`* run *newer than the staleness
cutoff* — a dead scraper must never manufacture removals (that would poison
days-on-market). A same-id reappearance flips `removed → relisted`.

**Run-status classification is what gates the sweep, so its tolerances matter.**
A run stays `ok` (and keeps authorizing removals) through a *low* item-failure
rate — the failures are noted in `run.error` but don't downgrade status, or a
scraper dropping one stub a day would silently freeze removal tracking forever.
It goes `anomalous` only on a real collapse: empty fetch, unparsed ratio over
`ANOMALY_UNPARSED_RATIO`, or volume under `ANOMALY_VOLUME_RATIO × trailing avg`;
`failed` only when nothing parsed. The trailing average counts `ok` runs only,
so anomalous/empty runs never poison it.

**Legal tags (`atlas/ingest/legal.py`) separate facts from claims.**
`rera_registered` is a verifiable join of `listings.rera_ids` against the
ingested registry. `khata_type`/`jurisdiction`/`layout_approval` are keyword
matches on listing text — `evidence.kind = 'listing_text_claim'`, explicitly
"NOT document-verified", status never better than `pass (claimed)`.
Document-verified checks are the separate, property-scoped `legal_checks` table
(Phase 3+). Never conflate the two.

**The Phase-1 gate is measured, not claimed** ([atlas/gate.py](atlas/gate.py),
`atlas.cli gate`, `GET /gate`). A day is clean when every enabled source *that
was live that day* landed an `ok` run, counted in **Asia/Kolkata**. Four rules
are load-bearing and each has a regression test: a retry later the same day
rescues the day; a newly-added source never retroactively dirties history it
couldn't participate in; a not-yet-collected today is `pending`, not dirty
(the jobs are staggered 05:30/06:00/06:45, so a check at 05:45 must not read
as broken); and `first_run_day` comes from **all** history, not the lookback
window — otherwise a source dead longer than the window vanishes and the gate
certifies Phase 1 on a dead scraper.

**Capital is modelled reserve-first** ([atlas/profile.py](atlas/profile.py),
`profile-v2`; [atlas/plan.py](atlas/plan.py)). `deployable = liquid_total −
reserved`; the emergency fund is never buying power, and `committed_inr` is
long-term equity tracked apart as *unlockable at a costed LTCG*. Three
invariants: **stamp duty + registration cannot be borrowed** (~6.65% above
₹45L, cash — a hard floor no financing removes); a `flag` on `khata_type` or
`layout_approval` makes a property un-financeable and collapses the ticket to
cash, so affordability and the legal tag are **coupled**; and
`months_until_affordable()` returns `None` when savings never catch the market,
which is a decision ("buy smaller/further out now"), not an error. Capital is
config (`ATLAS_LIQUID_TOTAL_INR`, `ATLAS_RESERVED_INR`,
`ATLAS_MONTHLY_CONTRIBUTION_INR`, `ATLAS_COMMITTED_INR`) because it changes —
and the briefing must always print the figures it assumed, so a stale value is
visible daily instead of silently mis-filtering.

**Deal Score is renormalised, never zero-filled** ([atlas/scoring/](atlas/scoring/)).
Weights live in `weights.py` and are mirrored into `score_weights`;
`ensure_weights()` raises `WeightsDriftError` if a stored version disagrees
with the code, so changing how deals rank forces a version bump. A factor with
no data for a listing **abstains** (`None`) — it is still written as a
`score_factors` row explaining why, and `overall` is renormalised over the
weight that was actually covered, recorded as `scores.coverage`. Scoring 0 for
"unknown" would punish listings for Atlas's own gaps; with three factors
dataless and price history days deep it would flatten the ranking entirely.
`guidance_value_gap` / `infra_proximity` / `rental_yield` are declared at
weight 0 with a stated reason and printed in every briefing — the roadmap
calls the guidance-value gap the core arbitrage signal and it was never built,
so `price_vs_locality` is labelled a *stand-in*, not a substitute. Scores are
listing-scoped (migration 0003) because `properties` is empty until Phase 4,
and idempotent within an `Asia/Kolkata` day via `score_date`, so a
recommendation already emailed keeps the number that was sent.

**Locality medians are segmented by asset class.** Land and built stock are
priced on different bases; a mixed median would make every plot read as a ~45%
discount and dominate the ranking on an artefact.

**`seller_motivation` is asynchronous and optional**
([atlas/scoring/motivation.py](atlas/scoring/motivation.py)). Haiku over the
Batch API answers in minutes to hours, so submission and collection are
separate passes over `listing_motivation` and the factor abstains until a
result lands. With no `ANTHROPIC_API_KEY` it abstains for every listing — a
missing key must never look like "no seller here is motivated".

**Parsers may return three things** ([atlas/ingest/parsers/](atlas/ingest/parsers/)):
a dict, `None` (a genuine failure, counted against the unparsed ratio), or
`SKIP` (a valid record that is not a listing — 99acres mixes builder projects
into the feed). Skips are excluded from that ratio, or a project-heavy feed
would cross `ANOMALY_UNPARSED_RATIO`, mark healthy runs `anomalous`, and
freeze the staleness sweep. `SourceSpec.enabled` is mirrored to
`sources.enabled`, which the gate reads — the paid 99acres specs ship
**disabled** so they neither bill nor owe a daily `ok` run until switched on.

**`listings.posted_at` is the portal's date, not ours.** `first_seen_at` only
means "when Atlas noticed", so using it for days-on-market reads every listing
as brand new. `atlas.cli reparse` replays `raw_payloads` to backfill it — the
first real exercise of the raw-first guarantee, and written to be reusable for
any future parser fix.

**The briefing states the capital it assumed, first, always**
([atlas/report.py](atlas/report.py), 07:15 IST). Capital is env config, so a
stale figure mis-filters in both directions; printing it daily is what makes
it visible. Nothing unfundable is ever recommended (Phase 2b), quiet days
still send (silence is indistinguishable from a dead cron), and delivery is
guarded by `report_runs.sent_at` — `UNIQUE(report_date)` stops a duplicate
row, not a duplicate email. `digest_daily` is deliberately **not** in
`run_daily()`, because that sequence is what the startup catch-up replays.

**The email is a product surface, not a log dump**
([atlas/emailer.py](atlas/emailer.py), split from report.py). Table layout and
inline styles because Gmail strips `<head>` styles and Outlook renders through
Word. Factor names are translated for the reader (`Affordability`, not
`capital_fit`) and only the top three reasons are shown — six is an audit
trail, and that lives in `score --explain`. **Internal identifiers must never
reach it**: a test fails if `handoff`, `atlas_roadmap`, `PostGIS`, `§` or a raw
factor key appears in the rendered briefing. Both renderers share
`_factor_line` so the text and HTML parts cannot drift. When nothing is
fundable — the normal state for months at an early capital position — it shows
the *ladder* rather than an empty page, with each row's distance in months so
it reads as a target, not an offer.

**Rupees are formatted Indian-style** ([atlas/money.py](atlas/money.py)):
`95,49,795`, never `9,549,795`. One implementation; `grep ':,}'` over `atlas/`
should return nothing.

**Config defaults follow one rule** (`deploy/compose-snippet.yml`, enforced by
[tests/test_deploy_config.py](tests/test_deploy_config.py)): **a compose
default is allowed only where being wrong errs conservative.**
`ATLAS_LIQUID_TOTAL_INR` / `ATLAS_RESERVED_INR` set the purchase ceiling, so a
wrong value over-promises — they carry no default and the deploy stops. Savings
and committed capital default to 0 because that under-promises. Every setting
whose absence is *silent* must appear in the snippet; the app reading a value
its container never receives is a bug this project has now hit twice.

**Auth:** every endpoint except `GET /health` requires `Authorization: Bearer $ATLAS_API_TOKEN`; an *unset* token must lock the API (503), never open it. The one exception is `GET /feedback/{id}/{up|down}` — a mail client cannot send a header, so those links carry an HMAC over **both** the id and the vote (so a link cannot be edited into its opposite) and fail closed when no secret is set.

**Deploy is image-only.** The dev box has no Docker, so CI
([.github/workflows/release.yml](.github/workflows/release.yml)) builds and
publishes to `ghcr.io`; the VPS holds no source and pastes
[deploy/compose-snippet.yml](deploy/compose-snippet.yml) into its own
multi-service compose (`atlas-db`/`atlas-app`, namespaced `ATLAS_*` env keys,
no published ports, routed by the user's existing Traefik). `docker-compose.yml`
has no `build:` for the app on purpose — building is the dev-box overlay
`docker-compose.build.yml`. Runbook: [docs/deploy-vps.md](docs/deploy-vps.md).
Note APScheduler uses an **in-memory** jobstore, so downtime across 05:30 IST
would lose a day; `jobs.catch_up_if_missed()` collects on boot instead.

## Project rules (from the plans; treat as review criteria)

- Raw first, parse second: never parse before archiving; parser bugs must be recoverable by re-parsing `raw_payloads`.
- Failures are recorded, never swallowed: a run ends `ok`/`anomalous`/`failed` with `finished_at` set — silent failure is the enemy, and "no new listings" must be distinguishable from "the scraper is dead".
- Version everything that judges: bump `PARSER_VERSION` on any mapping change (it's stamped on every row); `WEIGHTS_VERSION` on any scoring change (enforced — `ensure_weights` raises on drift); `MOTIVATION_PROMPT_VERSION` on any prompt, schema, or signal-vocabulary change (cached extractions are keyed on it).
- Golden files ([tests/golden/](tests/golden/)) are *reviewed artifacts*: regenerate only deliberately and inspect the diff — never blindly to make tests pass.
- Evidence or it didn't happen: scores/flags/recommendations store the factor rows and source references that produced them.
- All scheduled jobs run in `Asia/Kolkata` explicitly.
- Postgres binds to localhost only; the DB's accumulated history is the asset — raw archive + nightly `pg_dump` (scripts/backup.sh) make it rebuildable.
