# Real Estate Intelligence Agent — Build Plan

> Companion to [handoff.md](handoff.md). The handoff describes the destination; this document describes how to get there without building an unreliable system along the way.

---

## 1. Objective (unchanged)

A personal AI acquisition engine for the Bangalore market (later Mysore / Karnataka) that:

- Collects listing, builder, transaction, and infrastructure data continuously.
- Detects opportunities (distress, price capitulation, undervalued micro-markets, JD potential).
- Delivers a short, high-precision daily briefing with cited evidence.
- Remembers every broker conversation and nudges follow-ups.

**AI automates research; the human does visits, negotiation, and decisions.**

### Decisions log (2026-07-19)

| Decision | Choice | Consequence |
|---|---|---|
| Market scope | **All Bangalore** (all localities, apartments + plots) | Higher volume; LLM extraction runs selectively to control cost; locality index covers full city |
| Briefing delivery | **Email** (Resend/SES free tier) | Instant-tier alerts also via email initially; Telegram optional later |
| Hosting | **User's existing VPS** | $0 hosting; auth + HTTPS (Caddy) required from day one |
| Budget posture | Start at the cheap end (~$25–40/mo), widen only if signals are missed | Free actors first; selective LLM extraction; $/day spend shown in the daily report |

---

## 2. Design Principles

1. **Evidence or it didn't happen.** Every score, flag, and recommendation stores the factors and source rows that produced it. No uncited AI claims enter the database.
2. **Raw first, parse second.** Every scrape run stores the raw payload before extraction. Parsers can break and be re-run; data loss is unacceptable, parse bugs are recoverable.
3. **Silent failure is the enemy.** Every data source has expected-volume monitoring. "No new listings" must be distinguishable from "the scraper is dead."
4. **Precision over recall in the report.** Quiet days are allowed. Diluted signal kills trust in the tool.
5. **One database, few moving parts.** Complexity is added only when a measured limit forces it.
6. **Official data before scraped data.** RERA, Kaveri registrations, and guidance values are structured, legal, and higher-signal than portal listings.
7. **Version everything that judges.** Scoring weights, prompts, and parser versions are recorded so score changes are attributable to the market or to the algorithm — never ambiguous.

---

## 3. Tech Stack (trimmed from handoff)

| Concern | v1 choice | Graduate to (only if forced) |
|---|---|---|
| Backend | Python + FastAPI | — |
| Database | PostgreSQL 16 + pgvector | + PostGIS when map features land |
| Search | Postgres full-text search | Meilisearch if FTS proves insufficient |
| Scheduling | APScheduler (in-process) | Celery + Redis at real queue scale |
| Scraping | Apify actors + custom fetchers | — |
| LLM | Claude only — Haiku for extraction, Sonnet/Opus for analysis | Local models if cost demands |
| Delivery | Email via Resend/SES free tier (digest + urgent alerts) | Telegram/WhatsApp bot |
| Deployment | User's existing VPS, Docker Compose, Caddy (HTTPS + auth) | — |

**Explicitly deferred:** Temporal, Meilisearch/Typesense, Redis, multi-provider LLM routing, microservices. Migrating up later is easy; debugging five services when the 7am report fails is not.

---

## 4. Data Source Strategy (priority order)

| Priority | Source | Type | Why |
|---|---|---|---|
| 1 | Karnataka RERA | Official, public | Builder registrations, project timelines, complaints. Powers builder intelligence legally and structurally. |
| 2 | Listing portals (MagicBricks, 99acres, Housing, NoBroker) | Apify actors | Volume and freshness — actor availability validated (below). Never the single point of truth. |
| 3 | Guidance values + per-property Kaveri EC pulls | Official, semi-manual | Kaveri 2.0 is OTP-authenticated per request with no public API — **bulk transaction ingestion is not feasible.** Use guidance values for locality baselines and pull ECs per shortlisted property at diligence time. |
| 4 | Government notifications (BBMP/BDA/BMRDA/Metro) | Official, public | Infrastructure signals for locality scoring. |
| 5 | Builder websites, news | Scraped/monitored | Launch and stress signals. |

All scraping respects ToS and applicable law; prefer official APIs/datasets wherever they exist. Portal coverage starts with **one** portal and expands only after the pipeline is proven.

### Validation findings (checked 2026-07-19)

**Portal actors exist and are healthy.** The Apify store has working actors for all four portals; one developer (`thirdwatch`) maintains **free** actors for three of them with a consistent input schema (`searchMode`, `city`, `localities`, `bhk`, price/area filters), which simplifies M1 integration considerably:

| Portal | Primary actor | Pricing | Success rate | Fallback actor |
|---|---|---|---|---|
| MagicBricks | `thirdwatch/magicbricks-scraper` (25+ fields incl. RERA ID, GPS) | Free | 90.6% | `memo23/magicbricks-property-scraper` ($1.2/1k, 98.2%) |
| 99acres | `thirdwatch/acres99-scraper` (40+ fields incl. lat/long, RERA ID) | Free | 88.3% | `fatihtahta/99acres-scraper-ppe` ($3.5/1k, 98.5%) |
| NoBroker | `thirdwatch/nobroker-scraper` (owner-direct, Bangalore supported) | Free | 90.8% | `parseforge/nobroker-scraper` (50+ fields, $12/1k, 97.6%) |
| Housing.com | `unfenced-group/housing-scraper` (GPS, possession date) | $0.8/1k | 90.6% | `abotapi/housing-com-scraper` ($1/1k, 97.9%) |

Bonus source: Square Yards actors also exist (`vladignatyev/squareyards-scraper`, $0.9/1k) for a fifth portal later. Free-actor success rates (~90%) reinforce the raw-payload + health-monitoring design — failures are expected and recoverable.

**Karnataka RERA — confirmed fully public.** No login or fee; 8,357+ registered projects and 5,417+ agents; searchable by project, promoter, district, registration number. Project pages expose promoter details, approved plans, declared completion dates, quarterly construction progress, and complaint/litigation history (statuses updated weekly). M4 (Builder Intelligence) is on solid legal and structural ground.

**Kaveri — constrained, plan adjusted.** Kaveri 2.0 (kaveri.karnataka.gov.in) issues digitally signed ECs but requires OTP-based authentication per request; no API or bulk export. Consequence: the "registered transaction comparables engine" is demoted from a bulk pipeline to (a) guidance-value baselines per locality and (b) semi-automated per-property EC pulls when a property is shortlisted — which is when the EC matters for the legal checklist anyway.

---

## 5. Module Map (revised)

| # | Module | Change from handoff |
|---|---|---|
| M1 | Listing Collector | + raw payload archive, + source health monitoring |
| M2 | **Entity Resolution** | **New — most important addition.** Cross-portal identity: one property, many listings. Full design (pipeline, features, thresholds, evaluation plan): [docs/entity-resolution.md](docs/entity-resolution.md). |
| M3 | Price & Market Tracker | + guidance-value baselines and per-property Kaveri EC pulls (bulk transaction ingestion isn't feasible — see §4 validation findings), + locality micro-market index (₹/sqft trends, inventory, absorption per area) |
| M4 | Builder Intelligence | RERA-first instead of scrape-first |
| M5 | Contacts & Memory | Merged handoff M4+M5. Broker CRM with embedded, searchable interaction notes. Ten-second phone capture or it won't be used. |
| M6 | Property Analyzer | + legal risk checklist (khata type, EC status, title flags) promoted from "future" |
| M7 | Deal Score | + stored factor decomposition, + versioned weights, + **outcome tracking** (did it sell? at what discount? did I visit and pass — why?) enabling backtests |
| M8 | Government Watch | Unchanged (weekly digest) |
| M9 | Intelligence Report | + two tiers: instant push for rare high-conviction signals, daily digest for the rest. + 👍/👎 feedback per recommendation to tune thresholds. |

---

## 6. Phased Roadmap

> Estimates are **effort-hours, not calendar time** — calendar duration depends on hours available per week (at ~10 focused hrs/week, Phases 0–2 ≈ 7–9 weeks to a working daily briefing). Each phase's "done when" gate is what matters, not the clock.

### Phase 0 — Foundations (~10–15 hours)
- Repo, Docker Compose (FastAPI + Postgres), migrations (Alembic), config, CI with parser golden-fixture tests.
- Core schema designed in [docs/schema.sql](docs/schema.sql) — ingestion spine, listings/versions, resolved entities, versioned scoring with factor evidence, builders/RERA, localities, contacts/interactions, outcomes and report runs.

**Done when:** schema migrated, one fake source flows end-to-end raw → parsed → stored.

### Phase 1 — Data Spine (~30–40 hours)
- M1 for **one portal** — start with `thirdwatch/magicbricks-scraper` (free, validated; see §4) + Karnataka RERA ingestion.
- Change tracking: new / updated / price-changed / removed / relisted.
- Source health monitoring with expected-volume alerts.
- Basic price-drop detection (5% / 10% / repeated / stale-on-market).

**Done when:** 7 consecutive days of clean ingestion; a deliberate parser break is caught by monitoring, fixed, and history re-parsed from raw payloads.

### Phase 2 — The Product: Daily Briefing (~25–35 hours)
- Deal Score v1: transparent weighted factors, stored decomposition, versioned weights.
- Daily email digest: top opportunities with cited evidence, price changes, new listings. Strict thresholds — quiet days allowed.
- 👍/👎 feedback capture per recommendation.

**Done when:** the briefing arrives every morning for 14 days and at least one recommendation per week is worth reading.

### Phase 3 — Intelligence Layers (~50–70 hours)
- M2 Entity Resolution across a second portal, per [docs/entity-resolution.md](docs/entity-resolution.md).
- Guidance-value baselines + locality micro-market index; semi-automated per-property EC pull workflow (Kaveri bulk access ruled out — §4).
- M4 Builder Intelligence (RERA + news summarization with citations).
- M6 Property Analyzer (URL in → structured analysis out, legal checklist included).
- Instant-tier push alerts.

**Done when:** cross-portal duplicates merge correctly on a hand-labeled sample (target ≥90% precision), and comparables appear in property analyses.

### Phase 4 — Relationship Layer (~20–30 hours)
- M5 Contacts & Memory: quick capture endpoint, embedding + retrieval, follow-up nudges in the digest.
- M8 Government Watch weekly summary.

**Done when:** a note captured on the phone in <15 seconds surfaces automatically in the relevant property's analysis and the digest.

### Phase 5 — Learning Loop (ongoing)
- Outcome recording (sold / withdrawn / visited-passed / bought) and days-on-market truth.
- Backtesting harness: replay historical data against candidate scoring weights.
- Threshold tuning from 👍/👎 history.

### Future (unchanged from handoff)
WhatsApp/Gmail/Calendar integration, voice notes, document OCR (EC/RTC parsing), map overlays, satellite imagery, construction cost estimation, JD feasibility calculator.

---

## 7. Reliability Engineering (cross-cutting)

- **Parsing:** golden fixtures per source; parser version stamped on every extracted row; re-parse command over raw archive.
- **Monitoring:** per-source run dashboard (counts, durations, failures); alert on volume anomaly or consecutive failures; the daily report *states* source health so absence of news is never ambiguous.
- **LLM calls:** typed Pydantic output schemas, validation before DB write, prompt versioning, response caching, small eval set of hand-checked cases re-run on prompt changes.
- **Jobs:** idempotent by design (re-running a day is safe); failures logged with payload references, never swallowed.
- **Data quality:** outlier guards (₹45L vs ₹4.5Cr typos), staleness timestamps on every fact, confidence field on resolved entities.
- **Backups:** nightly Postgres dump, tested restore. The raw payload archive makes the whole system rebuildable.
- **Security:** the VPS-hosted API sits behind Caddy with HTTPS and token auth from day one — it stores broker phone numbers and private deal notes. Postgres bound to localhost only.
- **Report delivery watchdog:** systemd auto-restart on the app, plus an external dead-man's switch (healthchecks.io free tier) — the report job pings on success, and a missed 7:00 IST ping alerts you. The ≥99% delivery metric is enforced, not hoped for.
- **Scheduling:** all jobs explicitly in Asia/Kolkata timezone.
- **Relist detection routes through entity resolution (M2):** portals often issue new listing IDs on relist, so naive ID matching silently resets days-on-market — a core distress signal. New listings are matched against recently-removed ones using the M2 pipeline before being counted as "new."

---

## 8. Cost Model (monthly, steady state)

Pricing verified against current Claude API rates (2026-07) and Apify store listings (§4).

Updated for the decisions log: all-Bangalore scope, user's existing VPS (hosting $0).

| Item | Assumption | Est. cost |
|---|---|---|
| LLM — extraction (Haiku 4.5, $1/$5 per MTok, **Batch API −50%**) | Actors return structured fields; LLM reads **descriptions only, selectively** (new/changed listings). All-Bangalore ≈ 500–1,500 new/changed listings/day → | ~$10–45 |
| LLM — analysis & report (Sonnet 5, $3/$15 per MTok; $2/$10 intro to Aug 2026) | Daily report ~50K in / 5K out + ~10 property deep-dives/week, prompt caching on stable context | ~$12–15 |
| LLM — builder/news summaries | Weekly, small | ~$3 |
| Apify platform compute | Free actors for 3 portals; free plan includes $5 credit — all-Bangalore daily volume may need the $39 Starter plan (measured in week 1) | $0–40 |
| Email | Resend/SES free tier | $0 |
| VPS hosting | Already owned | $0 |
| **Total** | | **~$25–90/month** (start at the cheap end) |

Cost controls built into the design: Haiku-only extraction, Batch API for all non-interactive LLM work, prompt caching on report generation, deterministic parsing first (LLM only where structure fails), and **actual $/day spend surfaced in the daily report itself** with a $5/day alert. Levers if cost runs hot: scrape every 2 days instead of daily, restrict LLM description-reading to flagged listings only (price drops, stale, distress keywords), trim to 2 portals.

### Measured, as of 2026-08-02 (Phase 2 built, not yet enabled)

The estimates above were made before anything ran. What the built system
actually costs:

| Item | Measured | vs. estimate |
|---|---|---|
| LLM — extraction (`seller_motivation`, Haiku 4.5 + Batch API) | **~$0.02–0.05/day** (~$1/mo). ~650 listings × ~250 tokens in / ~80 out, halved by Batch, and cached on `(listing_id, description hash, prompt version)` so only new or edited text is ever re-billed | **Far below** the $10–45 estimate, which assumed reading full descriptions daily rather than once per change |
| 99acres plot source (`fatihtahta/99acres-scraper-ppe`, $3.49/1k) | **~$0.98/day (~$29/mo)** — 7 corridor seeds × 40 results. `limit` is **per location**, so each extra seed adds its own 40 to the bill | New line item; not in the original table |
| MagicBricks + RERA | $0 (free actors, well inside the Apify free credit) | At the cheap end as hoped |
| LLM — analysis & report | **$0** — the digest is deterministic string rendering, no model call | Estimate assumed an LLM-written report; the briefing turned out better as a fixed format |
| Email (Resend), VPS | $0 | As estimated |
| **Total once fully enabled** | **~$30/month**, essentially all of it the plot actor | Inside the $25–90 band, but with the mix inverted: scraping dominates, not LLM |

The design lever that mattered most was not model choice — it was **caching
extraction on a content hash**. Re-scoring is free; only genuinely new or
edited listing text costs anything.

Still unbuilt from the controls list: `$/day spend surfaced in the report`
and the $5/day alert. Worth adding when spend is dominated by a *paid actor*
rather than the LLM, since the actor bill scales with corridor seeds and is
the one that can run away quietly.

## 9. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Portal blocks / actor breakage | Medium *(was High — actor availability validated §4; free-actor success ~90%)* | Multi-actor fallbacks per portal (free → paid); raw-payload archive; volume monitoring; degrade gracefully to fewer sources |
| Entity resolution accuracy | High | Full design + evaluation plan in [docs/entity-resolution.md](docs/entity-resolution.md): conservative thresholds, review queue, labeled-sample precision gate (≥95% on auto-merges), reversible merges |
| Kaveri bulk access | **Confirmed constraint** (no longer a risk) | Plan adjusted: guidance values + per-property EC pulls at diligence time (§4) |
| Report noise → user stops reading | High | Strict thresholds, feedback loop, quiet days allowed |
| Scope creep before Phase 2 ships | High | Nothing outside the current phase gets built; the briefing is the product |
| Free actors degrade or disappear | Medium | Paid fallbacks identified per portal (§4, ~$1–12 per 1k listings); budgeted in §8 |
| LLM cost drift | Low | Costed in §8 (~$35/mo LLM); tiered models, Batch API, caching, $5/day budget alert |

---

## 10. Success Metrics

- **System:** ≥99% of mornings the briefing arrives; source outages detected within 24h; zero unrecoverable data loss.
- **Signal:** ≥1 genuinely interesting opportunity surfaced per week (user-judged via feedback); false-positive rate on "urgent" alerts <20%.
- **Outcome (long-run):** documented cases where the system surfaced a deal before it was widely visible; hundreds of research hours saved per year (per handoff).
