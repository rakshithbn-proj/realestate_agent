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

## 3. Current reality (honest state — updated 2026-08-08)

> ## Where things actually stand
>
> **Phase 1: MET.** **Phase 2: built, deployed, and sending.** The daily
> briefing arrives; Resend returned 200 on 2026-08-07.
>
> **Read these five before doing anything.**
>
> **1. The capital position on the VPS no longer matches §9.1 of this file,
> and the discrepancy is unresolved.** Configured and live:
>
> | | |
> |---|---|
> | deployable | **₹1,00,000** (`ATLAS_LIQUID_TOTAL_INR` 3,50,000 − `ATLAS_RESERVED_INR` 2,50,000) |
> | saving | **₹45,000/month** |
> | committed | **₹7,00,000** |
> | ceiling | **₹3,00,661** |
>
> That totals ~₹10.5L against the **₹25L** recorded in §9.1, and the saving
> rate is ₹45k rather than ₹75k. Either the position genuinely changed since
> 2026-08-01 or the fields were mis-mapped — **`ATLAS_RESERVED_INR` is a slice
> *inside* `ATLAS_LIQUID_TOTAL_INR`, not a separate pot**, so entering the
> accessible figure as `liquid_total` and then reserving again understates
> deployable. Ask before trusting any runway number. At the configured
> values the nearest entry point is **23 months** away; at the §9.1 values it
> was 8.
>
> **2. Nothing is fundable, and will not be for ~23 months.** This is the
> correct answer, not a bug — but it means the briefing's opportunity section
> is empty every morning. It now shows the **ladder** (cheapest real entry
> points and what each requires) instead of an empty page.
>
> **3. The 99acres plot sources are still `enabled=False`.** The gate is 7/7
> so this is unblocked; it has simply not been done. Try the ~$1 manual run
> first (see "Before the next deploy" below) — it does not touch the streak.
>
> **4. `seller_motivation` was at 0% coverage as of 2026-08-02.** Expected on
> day one (the Batch API is asynchronous), but it should be populated by now.
> **If `atlas.cli score --dry-run` still shows 0%, `ATLAS_ANTHROPIC_API_KEY`
> was never set** and 8 points of weight are being silently redistributed.
>
> **5. Two commits are unpushed** (`556f2c1`, `fe8eaf3` — the email rebuild).
> The VPS is running the version *before* them.

> ## PHASE 1 IS MET — 7/7 clean days, 2026-08-01 → 2026-08-07
>
> Measured, not claimed: `atlas.cli gate` counts consecutive days on which
> every enabled source landed an `ok` run, in Asia/Kolkata, off the
> `scrape_runs` table. Seven consecutive CLEAN days across
> `magicbricks/bangalore`, `magicbricks/mysore` and
> `rera_karnataka/karnataka`, with **zero** bad runs on any source
> (`consecutive_bad_runs: 0` on all three) and no human intervention across
> the week. Volumes held steady: 300 / 145 / 9,817.
>
> This closes atlas_roadmap Phase 1's done-when. **Note it is a rolling
> measurement, not a banked award** — the gate re-computes from the last 7
> days on every call, so enabling a new source that then has a bad morning
> will drop it below 7 again. That is the intended behaviour (the point is
> current reliability, not a past trophy), but it is why the paid plot
> sources ship disabled.

> **Phase 2 is built and deployed** (239 tests). Deal Score v1, the 99acres
> plot source (disabled), and the Resend digest.

### What the deploy taught, 2026-08-02 → 08-08

Four things were found by *running* it, not by testing it — the same pattern
as the six in Phase 1. All fixed; each is worth not relearning.

- **The VPS never received the capital settings.** `deploy/compose-snippet.yml`
  passed `APIFY_TOKEN` and the scheduler flag but none of the
  `ATLAS_LIQUID_TOTAL_INR` family, so the app used its own defaults no matter
  what the VPS `.env` said — a ₹68L ceiling against a real ~₹10L. **This is
  the same bug class as "`.env.example` advertised capital overrides nothing
  read" in the Phase-1 list**: the fix went to `.env.example` and never to the
  compose passthrough. It surfaced only because the digest prints the capital
  it assumed. `tests/test_deploy_config.py` now fails if any silently-
  defaulting setting is unwired.
- **Compose defaults duplicated the ones in `atlas/config.py`.** The rule now
  encoded and tested: **a default belongs in compose only when being wrong
  errs conservative.** `ATLAS_LIQUID_TOTAL_INR` and `ATLAS_RESERVED_INR` set
  the ceiling, so wrong there *over-promises* — they use `:?` and the deploy
  stops. Savings and committed default to 0 because that under-promises.
- **Numbers were formatted in thousands, not lakhs.** `9,549,795` where an
  Indian reader expects `95,49,795`. One implementation in `atlas/money.py`;
  `grep ':,}'` over `atlas/` returns nothing.
- **The email was the terminal output in a `<pre>`,** with internal citations
  (`handoff §9.8`, `PostGIS`, `searchMode=buy`) leaking into the reader's
  inbox. Rebuilt as a real email in `atlas/emailer.py` — table layout, inline
  styles, listing links, feedback buttons, reader-facing factor labels. A test
  asserts those internal tokens can never reach the briefing again.

Also fixed: a Resend 403 crashed the CLI with a traceback because
`raise_for_status()` discards the response body — where Resend puts the actual
reason ("you can only send to your own address until a domain is verified").
Delivery failures now log the reason and return, leaving `sent_at` null so
tomorrow retries.

- **Deal Score v1 ships.** Six weighted factors, legal-first
  (`legal_risk` 30 / `capital_fit` 25 / `price_vs_locality` 15 /
  `thesis_fit` 12 / `distress` 10 / `seller_motivation` 8), versioned in
  `score_weights` with drift enforcement. The load-bearing choice is
  **abstention**: a factor with no data returns `None`, is still written as a
  `score_factors` row saying why, and `overall` renormalises over the covered
  weight (`scores.coverage`). Scores are listing-scoped — migration 0003 makes
  the subject nullable on both sides with a CHECK, rather than minting a fake
  `properties` row per listing and pre-empting Phase-4 entity resolution.
- **The plot source is wired and DISABLED.** `fatihtahta/99acres-scraper-ppe`,
  corridor-targeted (7 seeds × 40, ~$0.98/day). A real 24-item run is saved as
  a fixture. It found **plots at ₹33L–99L in Attibele and Sarjapur** — the
  first inventory Atlas collects that is actually inside the capital band.
  Rendered end to end, the briefing surfaces them at scores 76–79, every one
  fundable today against ₹35L deployable.
- **A third trap, not in the old §3 finding 1: coordinates are sometimes
  transposed** (`latitude: 77.618233, longitude: 13.002091` on a Sarjapur Road
  plot). A range check misses it — 77.6 is a legal latitude, just not in India
  — and the geohash lands in the Barents Sea. Handled in
  `parsers/common.normalise_coords`.
- **The m² trap is real and is now guarded by corroboration, not by trusting
  one field.** The record states the area four independent ways; agreement is
  what makes it trustworthy. The price cross-check is a **unit guard, not a
  tie-breaker** — on 4 of 24 fixture records all three area fields agree at
  1,200 sqft while `price / price_sqft` implies 1,280, because the seller
  rounded the *rate*. A first version preferred the arithmetic and silently
  invented a 7% larger plot; the golden file caught it.
- **The digest ships.** Capital block first on every path (naming the env var
  behind each figure), countdown, fundable-only opportunities with full
  decomposition, a watchlist of near-term targets, price drops, source health,
  gate streak, and an explicit "NOT SCORED — no data exists" section.
  Delivery is guarded by `report_runs.sent_at`; quiet days still send.
- **`listings.posted_at` landed**, with `atlas.cli reparse` to backfill it from
  the raw archive — the first real use of the raw-first guarantee.
- **Feedback loop wired**: signed 👍/👎 links write `recommendations.feedback`.
  This is the only route by which weights ever get tuned from evidence.

### Before the next deploy — two things need a human

1. ~~**Read `atlas.cli score --dry-run` before the first real scoring pass.**~~
   **DONE 2026-08-02 — weights v1 validated against production data, no change
   made.** Measured on 677 live listings:

   | factor | weight | coverage |
   |---|---|---|
   | `legal_risk` | 30 | 100% |
   | `capital_fit` | 25 | 98.4% |
   | `price_vs_locality` | 15 | **67.8%** |
   | `thesis_fit` | 12 | 100% |
   | `distress` | 10 | 100% |
   | `seller_motivation` | 8 | 0% (see below) |

   Distribution: min 24.1, **median 42.4**, max 82.8 — 20 listings above 60,
   325 in the 40–50 bulge. Genuinely discriminating, not crushed into one band.

   **`MIN_COMPS = 5` was the open question and it survives.** On the
   15-listing MagicBricks fixture `price_vs_locality` abstained 100% of the
   time; production has enough same-locality, same-asset-class comps to fire
   on two thirds of listings. No constant was changed, so weights v1 is the
   version that was reasoned *and* measured.

   **`distress` at 100% is the `reparse` backfill paying off**: 677/677
   listings got `posted_at` from the raw archive (`unmatched=0`), so
   days-on-market is real across the whole history rather than starting from
   the day the column landed. Without it this factor would have read 0%.

   **`seller_motivation` at 0% is expected on day one and is not a failure.**
   The Batch API is asynchronous: the 07:00 job submits a batch, and the
   results are collected by the *next* morning's run. It goes live on day 2 —
   provided `ATLAS_ANTHROPIC_API_KEY` is set. If it is still 0% on day 3, the
   key is missing and the factor is permanently abstaining.
2. **Do not enable the 99acres sources until the gate reads 7/7.** They ship
   `enabled=False` for exactly this reason: a newly enabled source must land an
   `ok` run every day from its first one, so switching them on mid-streak bets
   the Phase-1 clock on a scraper that has never run in production.

Also unset on the VPS and therefore inert: `ANTHROPIC_API_KEY` (so
`seller_motivation` abstains for every listing), `RESEND_API_KEY` /
`ATLAS_DIGEST_TO` (digest is built and stored but not delivered), and
`ATLAS_FEEDBACK_SECRET` / `ATLAS_PUBLIC_BASE_URL` (feedback links omitted).

### The capital block caught a live bug on its first run (2026-08-02)

The first `digest --dry-run` on the VPS printed **deployable ₹19,00,000 and
saving ₹0/month** against a real position of ~₹3.5L and ₹75k/month. Cause:
`deploy/compose-snippet.yml` never passed the `ATLAS_LIQUID_TOTAL_INR` /
`ATLAS_RESERVED_INR` / `ATLAS_MONTHLY_CONTRIBUTION_INR` / `ATLAS_LTV` /
`ATLAS_COMMITTED_*` keys into the container, so the app fell back to the
profile-v1 defaults no matter what the VPS `.env` said.

**This is the same bug class already in the record** — §3 lists "`.env.example`
advertised capital overrides nothing read" among the six found by running it.
That fix went to `.env.example`; the compose passthrough was never done, so the
setting was documented, settable, and ignored.

Nothing crashed, and nothing would have. Every affordability decision was
simply made against the wrong number: a ₹51.8L ceiling instead of ₹9.5L, so the
briefing would have recommended property that cannot be bought. The only reason
it surfaced is that the digest prints the figures it assumed at the top of every
send — which is precisely what roadmap Phase 2b asked for and why it is the
first thing on the page.

Fixed, plus `tests/test_deploy_config.py`: every setting whose absence is
*silent* must appear in the compose snippet, with a reverse check that a
newly-added setting cannot be left unwired. Settings that fail loudly
(`APIFY_TOKEN`) are excluded — a deploy that refuses to start is its own alarm.

---

## 3a. Previous state (2026-08-01)

- **Phase 0 (foundations): DONE.** FastAPI + Postgres 16 + Alembic + Docker
  Compose/Caddy spine; raw-first ingestion pipeline; token auth; the
  `docs/schema.sql` design migrated (migration 0001) with the multi-city columns
  and embeddings deferred (§4a / §9.4 decided — see the `atlas-phase0-decisions`
  memory). Local dev runs on portable Postgres (`.pgbin/`, no Docker); tests
  green in CI. `scripts/start.ps1` / `stop.ps1` run the local stack.
- **Phase 1 (data spine + legal guardrail): code done, runtime gate RUNNING.**
  Built: RERA collector ported and proven live; MagicBricks Bangalore **and
  Mysore** via Apify; new/updated/price-changed/removed/relisted tracking with
  the dead-scraper sweep guard; legal-risk tags v1 (`rera_registered` fact vs
  khata/jurisdiction/layout listing-text claims); per-source health;
  APScheduler + `python -m atlas.cli`. **110 tests passing, CI green.**
- **DEPLOYED AND COLLECTING AUTONOMOUSLY (2026-08-01). Gate 1/7** — all three
  sources `ok`; Phase 1 closes **2026-08-07** if unbroken. Verified on the box:
  300 bangalore + 147 mysore active listings, 8,869 `rera_projects` (the RERA
  count matched the local pull exactly, from an independent fetch). API live at
  `https://atlas.srv922449.hstgr.cloud` with a real Let's Encrypt cert;
  `/health` returns ok, `/gate` correctly returns **401** unauthenticated.
- **Deploy model: image-only, merged into an existing multi-service stack.**
  CI (`.github/workflows/release.yml`) publishes
  `ghcr.io/rakshithbn-proj/realestate_agent:latest` (package public). The VPS
  holds **no source** — just its own compose file, `.env`, and Atlas pasted in
  as `atlas-db`/`atlas-app` (see [deploy/compose-snippet.yml](deploy/compose-snippet.yml)),
  routed by the **user's own Traefik** (`mytlschallenge`, entrypoint
  `websecure`) to `atlas-app:8000`. There is no host port and no Atlas Caddy.
  Ship a change: push → CI builds → `docker compose pull atlas-app &&
  docker compose up -d atlas-app`. Full runbook: **[docs/deploy-vps.md](docs/deploy-vps.md)**.
  ⚠ The local Windows Scheduled Task `Atlas-Daily` is now redundant —
  **unregister it**, or you run two independent databases and pay Apify twice.
- **The clock is measured, not claimed** — `atlas.cli gate` and `GET /gate`
  count consecutive clean days off `scrape_runs` in **Asia/Kolkata**. Clean =
  every enabled source live that day landed an `ok` run. A retry rescues the
  day; a new source never retroactively dirties history it couldn't join; a
  not-yet-collected today is `pending`, not dirty; a source dead longer than
  the lookback window still counts against you (that last one was a bug — it
  would have certified Phase 1 on a dead scraper).
- **Six real bugs found by running it, not by testing it** (all fixed, all with
  regression tests): the Apify token rode in a `?token=` query param and httpx
  logs full URLs at INFO, so it was written to the logs every run; compose never
  passed `APIFY_TOKEN`/`ATLAS_ENABLE_SCHEDULER`, so a deploy would have come up
  with a dead scraper; a 30s Postgres wait failed after an unclean shutdown
  where crash recovery took 34s; **APScheduler's in-memory jobstore silently
  loses a day when the container is down at 05:30** (`misfire_grace_time` only
  covers a live process) — the app now catches up on boot; `.env.example`
  advertised capital overrides nothing read; and CI was broken by `pgserver`
  having no cp313 wheel plus `tests/` lacking `__init__.py`.
  The startup catch-up earned itself on day one: the first VPS boot logged
  *"2026-08-01 is not clean (no runs yet) and the 05:30 window has passed"* and
  collected immediately; and while the Apify token was still a placeholder the
  sweep guard correctly refused to manufacture removals from the failed runs.
- **What was proven earlier** (see §7): Karnataka RERA is free/public/ingestible;
  **99.6% of portal listings with a RERA id join to the registry**; MagicBricks is
  a reliable free portal; 99acres' actor is dead (skip it); BaankNet auctions are
  blocked pending a browser-capture step.
- **Business inputs: GIVEN (2026-08-01), encoded in
  [atlas/profile.py](atlas/profile.py) as `profile-v2`.** Corridors South-East +
  North + East (Bangalore) plus Mysore; both plots and apartments; email via
  **Resend** (not yet wired). Corridors are matched as normalised **substrings**
  — the portal emits `Sarjapur Road` / `Sarjapur` / `Sarjapura Attibele Road`
  for one corridor, so exact matching would discard most of the inventory.
- **Capital is modelled reserve-first, and the real position is tighter than it
  first looks.** ₹25L sits in mutual funds and stocks, but only **~₹3.5L is
  realistically withdrawable**; the rest is emergency fund plus long-term equity
  that won't be broken into. So `liquid_total − reserved = deployable`, with
  `committed_inr` tracked separately as *unlockable at a costed LTCG*, never
  lumped into the reserve. Three rules the model enforces:
  - **Purchase costs come out of own funds, not the loan.** Karnataka stamp duty
    + registration is ~6.65% above ₹45L, cash at the sub-registrar. This is the
    hard floor: **no amount of financing removes it.**
  - **Legal status gates financeability.** A B-khata / revenue-site flag makes a
    property largely un-financeable and collapses the ticket to cash.
    Affordability and the legal tag are coupled, not independent factors.
  - **`months_until_affordable()` can return `None`** — meaning savings never
    catch up at that appreciation rate. That is the decision-relevant answer
    ("buy smaller or further out **now**"), and a tool that only ever said
    "keep saving" would hide it.
- **`atlas.cli plan` is the Phase-2b product**: the ladder of cheapest *real*
  reachable listings per market, with the cash bar and countdown for each. At
  ₹3.5L accessible and ₹75k/month saved, against actually-collected data:
  **Mysore flat ₹27L → bar ₹9.3L → 8 months**; **Attibele plot ₹45.8L → bar
  ₹16.8L → 18 months (22 if the corridor runs +10%/yr)**. The Attibele plot is
  the thesis-aligned one; the Mysore flat is buy-and-hold, which §1 explicitly
  rejects.

### Three findings from the first live data pass (2026-08-01) — read before Phase 2

1. **No plots in the pipeline — but the fix is validated and approved, not yet
   wired.** All 521 Bangalore listings are `apartment` (514) / `builder-floor`
   (5) / `penthouse` (2): MagicBricks returns no land, which blocks the land/JD
   thesis outright. **The paid actor `fatihtahta/99acres-scraper-ppe` was
   test-run on 2026-08-01 and works** — 25 real Bangalore land listings, e.g.
   *Residential Plot, Tumkur Road ₹55.2L @ ₹4,600/sqft* and *Bidadi ₹51.6L*,
   with **canonical RERA ids** (`PRM/KA/RERA/...`, no prefix-stripping needed),
   GPS, seller type, and `posted_at` for days-on-market. Cost ~$0.0035/result
   (~$1/day at 300/day). **User has approved funding it.**
   ⚠ **Two traps to handle in the parser before wiring it in:**
   - `property.area.min_area_sqft` is actually **square metres** — it returns
     `111.4836` for a 1,200 sqft plot. Confirmed by arithmetic
     (₹5,520,001 ÷ ₹4,600/sqft = 1,200 sqft). Ingesting it naively corrupts
     every ₹/sqft figure by 10.76× and would silently poison the
     guidance-value gap and the whole affordability ranking.
   - The feed mixes **two record types** — individual resale listings (with
     `property_id`) and builder *projects* (without one). A project is not a
     purchasable unit and must not become a listing.
2. **The capital band barely intersects Bangalore.** Of 656 active listings,
   490 sit in target corridors but only **40 are affordable — 38 Mysore, 2
   Bangalore.** Bangalore's median listing is ₹2.26Cr against a ₹68L ceiling.
   This is real support for the Mysore thesis (§4a), with the caveat that a
   300-item actor sample skews to promoted/premium stock — directional, not a
   census.
3. **The khata dimension of the legal layer is currently inert.** Zero of 670
   tagged listings carry any khata claim in their text, so `khata_type` is
   `unknown` everywhere and the financeability coupling never fires today. It is
   correct and will matter for plots (where khata language is common in
   listings); it does nothing for apartment descriptions. `rera_registered`, by
   contrast, passes on 503/670 (75%) — the RERA join is carrying the layer.

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

## 9. Open decisions

**Answered 2026-08-01** — all encoded in [atlas/profile.py](atlas/profile.py)
(`profile-v2`; bump `PROFILE_VERSION` on any change, same discipline as
`PARSER_VERSION`):

1. **Capital** — ₹25L in mutual funds and stocks, but **only ~₹3.5L is
   realistically withdrawable**; the rest is emergency fund plus long-term
   equity. Saving monthly from today. Modelled reserve-first: `deployable =
   liquid_total − reserved`, `committed_inr` unlockable at a costed LTCG.
   ~70% LTV where financing is available. **Wealth accumulation is step one —
   the first purchase is 8–18 months out**, so until the cash floor is cleared
   the briefing's job is the countdown and the corridor, not "buy this"
   (roadmap Phase 2b).
2. **Target localities** — Bangalore South-East (Sarjapur/Attibele/Chandapura/
   Electronic City/Bommasandra), North (Devanahalli/Yelahanka/Hennur/
   Thanisandra/Jakkur), East (Whitefield/Varthur/Budigere/Hoskote/KR Puram),
   **plus Mysore** (not corridor-segmented — that's Phase-4 config per §4a).
   Matched as normalised **substrings**: the portal emits one corridor under
   many spellings (`Sarjapur Road` / `Sarjapur` / `Sarjapura Attibele Road`;
   `Electronic City` / `Electronics City Phase 1`), so exact matching would
   discard most of the inventory it is meant to select.
3. **Plots vs apartments** — both, equal weight. **But see §3 finding 1: there
   is currently zero plot inventory**, so this is aspirational until a
   plot-capable source lands.
4. **Ticket-size band** — falls out of (1) rather than being set separately:
   below ~₹20L is noise, above ₹68L is unfinanceable fantasy.
5. **Email provider** — **Resend** (one API key, 3k/month free, no domain
   verification needed to send to yourself). Not yet wired.

**Still open:**

6. ~~**Wire the plot source.**~~ **DONE 2026-08-02** — parser, fixture, and
   registry specs are in, shipped `enabled=False`. Enable after the gate
   reads 7/7. All three traps (m², project split, transposed coordinates) are
   handled with cross-checks and regression tests.
7. **Plot-loan LTV and loan eligibility.** The 70% LTV is a generic assumption.
   Plot loans are stricter (lower LTV, approved layout and clean title
   required, revenue sites often refused), and banks also size on income/FOIR,
   which the model does not know. **Worth one conversation with a banker** — it
   moves the ceiling materially, and it is a one-line config change afterwards.
8. **Guidance values were never built** despite the roadmap calling the
   guidance-value gap the core arbitrage signal (§4.8). Deal Score v1 shipped
   **without** it and says so: `guidance_value_gap` is a declared zero-weight
   factor with a stated reason, written on every score and printed in every
   briefing. `price_vs_locality` is a peer-relative *stand-in* and is labelled
   as one — it moves with the same sentiment it is trying to measure, which is
   exactly what the statutory anchor would avoid. **Next step (agreed
   2026-08-02): a timeboxed spike** on whether Kaveri/SRO guidance values are
   bulk-obtainable for the target corridors. If yes, a new source plus a real
   factor at weights v2. If no, document it as dead and stop calling it the
   core signal in the roadmap.
7. **Embedding model + vector dimension** — deferred to Phase 3, *not* needed at
   migration time. The schema assumes Voyage `voyage-3.5` (1024-dim);
   embeddings are a separate provider from Claude. The `vector` columns were
   deliberately left out of migration 0001, so the schema stays runnable on
   stock Postgres 16 and local tests keep working. Pin the dimension when
   semantic search actually lands; getting it wrong means a re-embedding pass.

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

> "Continue building Project Atlas. Read handoff.md §3 first (current state),
> then CLAUDE.md, atlas_roadmap.md (note Phase 2b — Capital Plan), and my
> auto-memory (atlas-progress, atlas-open-inputs). Confirm back where we are in
> one short paragraph before doing anything.
>
> State: **Phase 1 MET** (7/7 clean days). **Phase 2 built, deployed and
> sending** — Deal Score v1, the 99acres plot source (still disabled), and the
> Resend daily briefing. 239 tests green.
>
> Read handoff.md §3 'Where things actually stand' first. It lists five things
> to check before touching anything — most importantly that the capital figures
> configured on the VPS do not match §9.1 of the same file, so no runway number
> should be trusted until I confirm which is right.
>
> Then pick from §3 'What to do next', in that order."

### What to do next (2026-08-08), in order

**0. Push and deploy the two outstanding commits.** `556f2c1` + `fe8eaf3` (the
email rebuild) are committed locally but not pushed; the VPS is a version
behind and still sends the old `<pre>` email.

```sh
git push
docker compose pull atlas-app && docker compose up -d atlas-app
docker compose exec atlas-app python -m atlas.cli digest --force
```

**1. Settle the capital numbers.** See §3 item 1. Until that is resolved every
runway figure the system prints is suspect, and it is the input the whole
briefing filters on. `atlas.cli plan` is the check — the answer should match a
runway the user recognises.

**2. Confirm `seller_motivation` is populating.** `atlas.cli score --dry-run`;
if coverage is still 0%, `ATLAS_ANTHROPIC_API_KEY` is unset.

**3. Try the plot source, then decide on daily.** One manual run costs ~$1 and
does not join the gate (see "Before the next deploy"). Only enable
`enabled=True` afterwards, accepting ~$29/month and gate exposure.

**4. Surface spend in the briefing.** plan.md §8 always specified "$/day in the
daily report, with a $5/day alert" and it was never built. It mattered less
when cost was LLM tokens; now the bill is a paid actor whose cost scales with
the number of corridor seeds in a config file, and nothing warns you.

**5. The guidance-value spike (§9.8).** The last declared-but-missing factor,
and the one the roadmap calls the core arbitrage signal.

| | |
|---|---|
| **Running** | VPS: collect 05:30/06:00/06:45, score 07:00, briefing 07:15 IST |
| **Useful** | `gate` · `plan` · `score --dry-run` / `--explain <id>` · `top` · `digest --dry-run` · `health` · `reparse` |
| **Chores** | Unregister local `Atlas-Daily`; rotate the Apify token; pin `image: traefik` to a version |
| **Ask a human** | A banker, about real plot-loan LTV — it moves the ceiling more than any code here. Income + existing EMIs would also let `capital_fit` model FOIR instead of assuming 70% LTV is always available |
