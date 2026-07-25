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
.venv\Scripts\python -m atlas.cli run rera            # RERA registry (free, no token)
.venv\Scripts\python -m atlas.cli run magicbricks     # portal (needs APIFY_TOKEN)
.venv\Scripts\python -m atlas.cli sweep-and-tag       # staleness sweep + legal tagging
.venv\Scripts\python -m atlas.cli health              # per-source health JSON
```

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
listings removed when the source has a healthy run *newer than the staleness
cutoff* — a dead scraper must never manufacture removals (that would poison
days-on-market). A same-id reappearance flips `removed → relisted`.

**Legal tags (`atlas/ingest/legal.py`) separate facts from claims.**
`rera_registered` is a verifiable join of `listings.rera_ids` against the
ingested registry. `khata_type`/`jurisdiction`/`layout_approval` are keyword
matches on listing text — `evidence.kind = 'listing_text_claim'`, explicitly
"NOT document-verified", status never better than `pass (claimed)`.
Document-verified checks are the separate, property-scoped `legal_checks` table
(Phase 3+). Never conflate the two.

**Auth:** every endpoint except `GET /health` requires `Authorization: Bearer $ATLAS_API_TOKEN`; an *unset* token must lock the API (503), never open it.

## Project rules (from the plans; treat as review criteria)

- Raw first, parse second: never parse before archiving; parser bugs must be recoverable by re-parsing `raw_payloads`.
- Failures are recorded, never swallowed: a run ends `ok`/`anomalous`/`failed` with `finished_at` set — silent failure is the enemy, and "no new listings" must be distinguishable from "the scraper is dead".
- Version everything that judges: bump `PARSER_VERSION` on any mapping change (it's stamped on every row); scoring weights and prompts are versioned tables.
- Golden files ([tests/golden/](tests/golden/)) are *reviewed artifacts*: regenerate only deliberately and inspect the diff — never blindly to make tests pass.
- Evidence or it didn't happen: scores/flags/recommendations store the factor rows and source references that produced them.
- All scheduled jobs run in `Asia/Kolkata` explicitly.
- Postgres binds to localhost only; the DB's accumulated history is the asset — raw archive + nightly `pg_dump` (scripts/backup.sh) make it rebuildable.
