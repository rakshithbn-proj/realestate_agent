# Atlas — Build Handoff

> **Read this first in a fresh chat.** It is the single starting point for building
> Project Atlas for real. It tells a new session what Atlas is, what is already
> true, what to build first, and every hard-won fact that should not be relearned.
>
> **Decision (2026-07-24):** the 7-day `trial/` spike is retired. We are building
> the real application from Phase 0. The trial's *findings* are preserved below;
> its scaffolding (SQLite, the Apify-cost dashboard) is not carried forward. The
> one exception is the **RERA collector** (`trial/sources/rera.py`) — it is
> validated, working, and gets **ported** into the real ingestion layer, not
> rebuilt from scratch.

---

## 1. What Atlas is (the mission)

Atlas is a personal AI operating system for building real estate wealth in
Karnataka over 10–15 years — **Bangalore first, Mysore data-ready from day one**
(see §4a). It is **not** a listings app and **not** a CRM.

- The product is not the software. The product is **you making better investment
  decisions than the average investor**, compounding over decades.
- AI automates research, data collection, summarisation, pattern detection,
  follow-ups, and opportunity ranking. **Humans do site visits, negotiation,
  relationships, and the final decision.** Atlas improves judgement; it never
  replaces it.
- Success is not data volume. Success is: one exceptional deal a year you'd
  otherwise miss, fewer catastrophic mistakes, faster diligence, and a
  compounding base of knowledge and relationships.

Full vision and philosophy: **[overall_plan.md](overall_plan.md)** (the "why").

---

## 2. Canonical documents (what is authoritative for what)

| Doc | Role | Use it for |
|---|---|---|
| **[overall_plan.md](overall_plan.md)** | The vision (why) | The 14-module north star, philosophy, the wealth-creation sequence |
| **[atlas_roadmap.md](atlas_roadmap.md)** | The phased plan (how) | Phase-by-phase build order, the 9/10 thesis, the Bangalore domain moat (Appendix A) |
| **[handoff.md](handoff.md)** (this file) | The launch point (start here) | Current state, first-session plan, validated findings, gotchas |
| **[docs/schema.sql](docs/schema.sql)** | Data-model design | The Postgres schema to migrate via Alembic in Phase 0 (~22 tables, covers most modules) |
| **[docs/entity-resolution.md](docs/entity-resolution.md)** | M2 design | Cross-portal dedup (one property, many listings) — Phase 4 |
| **[plan.md](plan.md)** | Reference (still live) | atlas_roadmap.md builds on it, doesn't replace it. Go here for the **detailed reliability engineering (§7), cost model (§8), risk register (§9)**, and the data-source validation findings (§4). Its phase *ordering* is superseded by atlas_roadmap.md |
| `trial/` | Retired | Reference only. Port `trial/sources/rera.py`; ignore the rest |

If any two docs conflict, precedence is: **atlas_roadmap.md → overall_plan.md → this file** for *plans*, and **this file** for *current facts*.

---

## 3. Current reality (honest state)

- **No real app exists yet.** No Postgres, no FastAPI app, no frontend, no LLM
  integration. The repo is planning docs plus a retired spike.
- **What was proven** (see §7): Karnataka RERA is free, public, and fully
  ingestible; **99.6% of portal listings with a RERA id join to the registry**;
  MagicBricks is a reliable free portal source; 99acres' actor is dead (skip it),
  and the BaankNet auction feed is blocked pending a browser-capture step.
- **Open business inputs not yet given** (needed by Phase 2, not before): your
  capital band, target localities, and plot-vs-apartment preference. Note that two
  *technical* decisions are needed earlier, at Phase 0 — the embedding dimension
  (§9.4) and the multi-city schema column (§4a) — both flagged where they bite.

---

## 4. The v1 stack (from atlas_roadmap.md)

| Concern | Choice | Graduate to (only if forced) |
|---|---|---|
| Backend | Python + FastAPI | — |
| Database | PostgreSQL 16 + `pgvector` + `pg_trgm` (both extensions required by schema.sql) | + PostGIS when map/buffer-zone features land (Phase 4) |
| Migrations | Alembic | — |
| Search | Postgres full-text search | Meilisearch only if FTS proves insufficient |
| Scheduling | APScheduler (in-process) | Celery + Redis at real queue scale |
| Scraping | Apify actors + direct official fetchers | — |
| LLM (reasoning) | **Claude only** — Haiku for extraction, Sonnet/Opus for analysis | — |
| **Embeddings** | **Separate model, NOT Claude** — Anthropic has no first-party embedding model. Voyage (e.g. `voyage-3.5`, 1024-dim) is the assumed default; the schema's `vector(1024)` columns must match whatever is chosen. | Local embedding model if cost demands |
| Delivery | Email (Resend/SES free tier) | Telegram/WhatsApp later |
| Deploy | User's VPS, Docker Compose, Caddy (HTTPS + token auth from day one) | — |

**Explicitly deferred:** Temporal, Meilisearch, Redis, multi-LLM routing,
microservices, Next.js frontend. The daily briefing is the product; a dashboard
comes later. Migrating up is easy; debugging five services when the 7am report
fails is not.

---

## 4a. Markets — Bangalore first, Mysore data-ready

**Principle: multi-city model from day one; Bangalore intelligence first; Mysore
data collected cheaply now so it can *earn* capital before any is committed.**

The user's thesis is that Mysore is an early-stage market ("the next Bangalore")
where good deals may be available — explicitly an intuition to *test with data*,
not a decided bet. Atlas's whole premise is knowledge before assets, so the right
move is to collect Mysore data early and let it validate or kill the thesis.

**What Mysore costs to include (mostly free):**
- **Karnataka RERA** — statewide registry; **Mysore projects already ingest** with
  zero extra work (we pull all ~8,840 projects unfiltered). District segmentation
  (Bangalore vs Mysore) comes with the per-project detail pass in Phase 4.
- **Guidance values** — per-SRO across all of Karnataka; Mysore SROs are in the
  same Kaveri system.
- **Portals** (MagicBricks/NoBroker) — one extra config entry (`city: "Mysore"`).
  Actors support any Indian city; Mysore volume is a fraction of Bangalore's, so
  marginal cost is small.
- **For an early-stage market, portal data is thin** — so RERA + guidance values +
  land records + relationships matter *more* in Mysore than in Bangalore. Atlas is
  well-positioned for exactly that.

**The one thing that is cheap now and expensive later — do it in Phase 0:**
The schema has a latent Bangalore-only assumption. `localities`
([docs/schema.sql](docs/schema.sql) §"Localities") has `name`, `aliases`, and a
directional `zone` (`'east'`/`'north'`) but **no city/market column**. Add a
`city` (or `market`) dimension to `localities` (and to `sources`) **before the
first migration**, so a "Mysore micro-market index" is cleanly separable from
Bangalore's. Retrofitting this later means a migration + backfill + re-segmenting
every locality metric. See §5 step 3a.

**Deferred to when you actually act in Mysore (Phase 3–4), NOT now:**
- **Legal/khata authorities differ:** Bangalore is BBMP + BDA; Mysore is **MCC
  (Mysore City Corporation) + MUDA (Mysore Urban Development Authority)** — same
  framework (A/B-khata, approved-layout vs revenue-site, buffer zones), different
  authorities and portals. Note MUDA's known site-allotment controversy as a
  risk-rule input.
- **Appreciation drivers differ entirely:** Bangalore runs on metro/PRR/ORR/
  airport; Mysore runs on the **Bangalore–Mysore Expressway** (~90-min corridor),
  industrial growth, and airport expansion. This is Phase-4 data/config, not
  architecture.

---

## 5. First session — Phase 0 (Foundations)

**Goal:** a real, deployable spine where one source flows end-to-end into Postgres.

Concrete steps:
1. **Project skeleton** — a proper Python package (`atlas/` or `app/`, not
   `trial/`): FastAPI app, config via env, `pyproject.toml`/`requirements.txt`,
   `.env.example`. Token auth + Caddy HTTPS assumed from the start (it will store
   private deal notes and broker numbers).
2. **Docker Compose** — FastAPI + Postgres 16 (with pgvector) + Caddy.
3. **Alembic** — initialise, then migrate **[docs/schema.sql](docs/schema.sql)**
   into the first migration. That file is already Postgres-shaped (serial, jsonb,
   timestamptz, vector) and covers the ingestion spine, listings/versions,
   resolved entities, versioned scoring with factor evidence, builders/RERA,
   localities, contacts/interactions, outcomes, and report runs. It needs the
   `vector` and `pg_trgm` extensions (`CREATE EXTENSION` at the top of the file).
   **Decide the embedding dimension before this migration** — the schema pins
   `vector(1024)` in three tables (listings, contacts, interactions) and its own
   header warns that changing dims later forces a column rebuild + re-embedding.
   If the embedding model isn't chosen yet, either pin 1024 (Voyage default) or
   migrate those `vector` columns as nullable placeholders and finalise them when
   semantic search actually lands (Phase 3+). The vector columns are not needed
   for Phase 1 ingestion.
3a. **Multi-city schema tweak — do it in this first migration** (see §4a): add a
   `city` / `market` column to `localities` (and `sources`), and don't rely on the
   Bangalore-directional `zone` field as the only geographic split. This makes
   Mysore (and any later Karnataka market) a data addition rather than a
   migration-plus-backfill later. Cheap now, expensive to retrofit.
4. **Reliability primitives from day one** (non-negotiable, per atlas_roadmap):
   raw-payload-first ingestion (store the raw scrape before parsing, so parser
   bugs are recoverable, not data loss); a `parser_version` stamp on extracted
   rows; per-source run/health logging; nightly `pg_dump` + a tested restore.
5. **CI** — golden-fixture parser tests (a saved sample → expected parsed rows).

**Done when:** the schema is migrated, the app boots under Docker Compose behind
Caddy with auth, and one fake/fixture source flows raw → parsed → stored in
Postgres, verified by a test.

---

## 6. Phase 1 — Data spine + Bangalore legal guardrail

Front-load the "don't lose money" layer before trusting any recommendation.

- **Port the RERA collector** (`trial/sources/rera.py` → the real ingestion
  layer). It is your Priority-1 source, free, no Apify. Logic to keep verbatim:
  single GET of `viewAllProjects` → gzip-archive the raw HTML → parse the four
  positionally-aligned embedded JS arrays → `canon_reg_no()` (strip portal
  prefixes) → `norm_promoter()` (dedup builders across name variants) → upsert.
  Target the designed `rera_projects` + `builders` tables in schema.sql.
- **One portal collector: MagicBricks** (Apify `thirdwatch/magicbricks-scraper`,
  free, validated: 300 items/run, GPS + `rera_id` on ~88%). Never the single
  source of truth. Change tracking: new / updated / price-changed / removed /
  relisted.
- **Bangalore Legal-Risk Layer v1** (the moat — see atlas_roadmap Appendix A):
  tag every listing, with cited source, on khata type (A/B/E/e-khata),
  jurisdiction (BBMP/BDA/BMRDA/panchayat), RERA-registered yes/no,
  approved-layout vs revenue-site. Crude-but-cited beats absent.
- **Guidance-value baselines** per locality (the arbitrage yardstick).
- **Source health monitoring** — "no new listings" must be distinguishable from
  "the scraper is dead."

**Done when:** 7 consecutive clean ingestion days into Postgres; every listing
carries a legal-risk tag + source; RERA joins to listings by registration number
(this already hits 99.6% — see §7); a deliberate parser break is caught by
monitoring and re-parsed from the raw archive.

After Phase 1, the sequence is Phase 2 (the Daily Briefing ships) → Phase 3
(Investment Committee + Decision Journal — the judgment loop) → Phase 4+.
Full detail in **[atlas_roadmap.md](atlas_roadmap.md)**.

---

## 7. Validated technical intelligence (do not relearn this)

Hard facts established by direct testing on 2026-07-20. These de-risk Phase 1.

**Karnataka RERA — the win.**
- `https://rera.karnataka.gov.in/viewAllProjects?language=en` server-renders the
  **entire** registry in one ~6 MB response. No login, no API key, no pagination
  to walk. ~9,800 rows, **8,840 registered** (the rest are in-flight applications
  with no registration number — skip them).
- The data is **not** in an HTML table. It is four parallel JS arrays built by
  `.push()` calls, positionally aligned: `applicationNameList` (ack no),
  `...List2` (registration no), `...List3` (project name), `...List4` (promoter).
  Parser must assert equal array lengths and fail loudly if they diverge.
- **Join to listings works at 99.6%.** MagicBricks emits ids like
  `TOR/PRM/KA/RERA/...`; the registry holds `PRM/KA/RERA/...`. Canonicalising on
  the `PRM/KA/RERA/...` substring lifted the join from 75% → 98.5% → 99.6%.
- **The marketing brand ≠ the legal promoter.** "Godrej Properties" listings
  resolve to `GODREJ SSPDL GREEN ACRES PRIVATE LIMITED`; "Ramky Estates" →
  `Royaume Estates Private Limited`. The promoter is who is actually accountable
  for complaints/litigation — that distinction is the whole point of Builder
  Intelligence.
- **Promoters fragment badly** in the raw data (casing, `&` vs `AND`, HTML
  entities, legal suffixes): Sobha appeared as 2 variants (→121 projects),
  Prestige 6, DS-MAX 7, KSDB 9. A `norm_promoter()` key is mandatory or builder
  track-record analysis is meaningless. Working implementation is in
  `trial/sources/rera.py`.
- **Not yet built:** RERA per-project *detail* pages (district, declared
  completion, quarterly progress, **complaints/litigation history**) are a
  separate ~8,800-request pass (one POST each). That pass is where Builder
  Intelligence gets its teeth — schedule it in Phase 4.

**Portal sources.**
- MagicBricks (`thirdwatch/magicbricks-scraper`): **reliable, free**, 300
  items/run, 100% parseable, GPS + `rera_id` present. Use as the Phase-1 portal.
- NoBroker (`thirdwatch/nobroker-scraper`): works but defaults `ownerOnly=true`
  → ~25 owner-direct listings. Owner-direct is the *higher-value* subset (no
  broker, seller contactable, negotiable) — keep, but set the flag explicitly.
- **99acres (`thirdwatch/acres99-scraper`): DEAD.** Reports "SUCCEEDED, 300
  items" while ~99% are empty stubs echoing the input config. Not a mapping bug —
  the actor returns no data. If 99acres is wanted later (it is the only actor
  supporting `propertyType="plot"`, relevant to the land/JD thesis), test the
  paid fallback `fatihtahta/99acres-scraper-ppe` ($3.5/1k) instead.

**Auctions / distressed inventory — BLOCKED, needs browser capture.**
- Distressed inventory is definitionally motivated sellers and core to the
  value-creation thesis. Two official sources exist: **IBAPI** (RBI-mandated, PSU
  bank SARFAESI auctions) and **BaankNet/eBKray** (IBBI-mandated liquidation
  assets since Apr 2025).
- IBAPI is ASP.NET WebForms (`__VIEWSTATE` postbacks + a T&C gate) — brittle.
- BaankNet has a hidden JSON API (`/eauction-psb/api/property-listing-data/`,
  unauthenticated, ~29,400 properties, rich records incl. `possessionType`).
  **But that endpoint ignores every pagination and filter parameter** — pages 1,
  2, 300 all return the identical 50 records. It is a fixed teaser, not a
  queryable feed. **Next step:** a Playwright session against the live site to
  capture the *real* request payload the Angular app sends. Do not retry blind
  parameter guessing.

**Source access models (architectural split).**
- **Bulk-listable** (drive a pipeline): RERA registry, guidance values by SRO,
  (eventually) auctions.
- **Per-property lookup only** (diligence-time, needs an id): Bhoomi RTC (survey
  no.), BBMP e-Aasthi (EPID — returns A/B-khata, GPS, tax-paid status), Kaveri EC
  (OTP-gated, no bulk). Build these as on-demand diligence tools in Phase 3+,
  driven by a shortlist — not as bulk collectors.

---

## 8. The domain moat (what makes it 9/10)

A price-drop bot is 6/10. The edge is local, Karnataka-specific intelligence —
built for Bangalore first, with Mysore's equivalents (MCC/MUDA, the
Bangalore–Mysore Expressway) added in Phase 4 per §4a. Detailed in
**[atlas_roadmap.md](atlas_roadmap.md) Appendix A**:
- **Legal-catastrophe avoidance** (highest weight): khata type, jurisdiction,
  approved-layout vs revenue-site, DC conversion, EC/litigation flags,
  rajakaluve/lake **buffer zones** (NGT demolition risk).
- **Appreciation drivers:** metro proximity by completion stage (with
  time-decay), PRR/STRR/airport corridor, guidance-value gap, zoning.
- **Deal/distress:** price-drop depth + repetition, days-on-market (protected via
  entity resolution across relists), seller motivation, JD/redevelopment upside.
- **Financing realism:** LTV/EMI, Karnataka stamp duty (~5–6%) + registration,
  rental yield (~3–4% — appreciation, not yield, is the game).

> **Verification stance:** metro stages, zoning status, and scheme legality
> change over time and may be past the model's training cutoff. The system must
> *pull and cite* current status, never assume. This is also the correct
> engineering posture.

---

## 9. Open decisions (needed by Phase 2, not blocking Phase 0/1)

1. **Capital band** you can actually deploy (and whether you're financing / your
   LTV). Without it, ranking is generic and the briefing surfaces things you
   can't buy.
2. **Target localities / micro-markets** (Bangalore and, when ready, Mysore — see
   §4a), and **plots vs apartments vs both**.
3. **Ticket-size band** worth surfacing (below = noise, above = fantasy).

**Needed at Phase 0 (migration time), not Phase 2:**

4. **Embedding model + vector dimension.** The schema assumes Voyage
   `voyage-3.5` (1024-dim). Embeddings are a separate provider from Claude. Pin
   the dimension to the chosen model before the first migration, or defer the
   `vector` columns (see §5 step 3). Getting this wrong means a re-embedding pass
   later.

---

## 10. Gotchas & operational notes

- **Timezone:** all scheduled jobs in `Asia/Kolkata`, explicitly.
- **Secrets:** Apify token, DB creds, email keys via env / `.env` (never
  committed). Postgres bound to localhost; API behind Caddy auth from day one.
- **Report delivery watchdog:** external dead-man's switch (healthchecks.io free
  tier) — the daily job pings on success; a missed ping alerts you. ≥99%
  delivery is enforced, not hoped for.
- **Connectors:** Gmail / Google Calendar / Drive connectors are currently
  **disconnected** — re-authorize in claude.ai connector settings before the
  Phase-5 off-market (email ingestion) work. Not needed for Phase 0/1.
- **LLM discipline (when it lands in Phase 2):** typed Pydantic output schemas,
  validation before DB write, prompt versioning, Batch API for non-interactive
  work, prompt caching, a small hand-checked eval set re-run on prompt changes.

---

## 11. Success metrics (how we know it's working)

- **System:** ≥99% of mornings the briefing arrives; source outages detected
  within 24h; zero unrecoverable data loss (raw archive makes it rebuildable).
- **Signal:** ≥1 genuinely interesting opportunity per week (you judge, via 👍/👎
  feedback); <20% false-positive rate on "urgent" alerts.
- **Outcome (long-run):** documented cases where Atlas surfaced a deal before it
  was widely visible; hundreds of research hours saved per year; and — the real
  point — measurably better decisions, tracked in the Decision Journal.

---

### First message to paste into the new chat

> "Read handoff.md, overall_plan.md, atlas_roadmap.md, and docs/schema.sql (and
> plan.md §7–§9 for reliability engineering, cost model, and risks). We're
> building the real Atlas app starting at Phase 0 (forget the trial). Confirm the
> plan back to me — including the embedding-dimension decision (§9) and the
> multi-city schema change for Bangalore + Mysore (§4a / §5 step 3a) — then
> scaffold the FastAPI + Postgres 16 (pgvector + pg_trgm) + Alembic + Docker
> Compose + Caddy project, migrate docs/schema.sql as the first Alembic migration
> (with the `city`/`market` column added), and get one fixture source flowing
> raw → parsed → stored with a passing test."
