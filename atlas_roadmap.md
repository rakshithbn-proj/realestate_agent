# Atlas — Phased Build Roadmap

> Companion to three existing docs, not a replacement for them:
> - [overall_plan.md](overall_plan.md) — the 14-module *vision* (the destination).
> - [handoff.md](handoff.md) — the original brief.
> - [plan.md](plan.md) — the *authoritative* trimmed v1 build plan + reliability engineering.
>
> This roadmap does two things `plan.md` intentionally left out:
> 1. It re-attaches the **wealth-creation modules** (Investment Committee, Decision Journal, Learning Engine, Portfolio) that `plan.md` deferred — because for a *wealth* engine, those *are* the product.
> 2. It front-loads the **Bangalore-specific intelligence** (legal-risk avoidance, infrastructure-appreciation modelling, off-market deal flow, capital-aware ranking) that turns a listing-alert bot into something a real Bangalore investor would rate 9/10.
>
> **Market scope:** the roadmap is written **Bangalore-first**. Mysore is a planned second Karnataka market — kept *data-ready* from Phase 0 (multi-city schema) and collected cheaply via the statewide sources (RERA, guidance values, portals), but its market-specific intelligence (MCC/MUDA legal authorities, the Bangalore–Mysore Expressway appreciation driver) is deferred to Phase 4. Details in [handoff.md](handoff.md) §4a. Where this doc says "Bangalore," read it as "the active market" — the *method* is market-agnostic; only the encoded specifics differ.

---

## 1. The 9/10 thesis — what separates a wealth engine from a listing bot

A 6/10 tool tells you: *"Price dropped 12% on this listing."*

A 9/10 tool tells you:

> *"This ₹78L plot in Sarjapur is 14% below guidance value, **A-khata**, sits **outside the rajakaluve buffer**, RERA-registered layout, 900m from the under-construction Yellow Line (opening ~18 months), seller is relocating (quick-sale language in 2 brokers' messages), fits your capital at 70% LTV (~₹23L down), and is comparable to 3 plots you passed on at higher ₹/sqft. Here is the bull/bear memo. Visit before Saturday — it relisted once already."*

Everything after the price drop is the alpha. It comes from **five layers** that `plan.md` treats as later/optional but which a Bangalore wealth-builder needs early:

| Layer | Why it's decisive in Bangalore specifically | Where `plan.md` puts it |
|---|---|---|
| **Legal-risk avoidance** | One B-khata / rajakaluve-buffer / litigated-title / revenue-site mistake wipes out years of gains. This is the #1 thing a local buyer checks *first*. | Phase 3 checklist (too late) |
| **Infrastructure-appreciation modelling** | Metro Phase 2/3 corridors, PRR/STRR, airport corridor drive appreciation far more than rental yield (Bangalore yields are only ~3–4%). Proximity + *time-to-completion* is the appreciation engine. | Passive weekly digest (M8) |
| **Off-market deal flow** | The best Bangalore deals never reach MagicBricks — they move through broker WhatsApp/phone. Relationships are the source, not a CRM afterthought. | Phase 4 CRM |
| **Capital-aware, value-creation ranking** | You stated *limited capital*. A generic score that surfaces ₹5Cr villas is noise. Rank by what you can finance and by value-creation upside (JD, redevelopment, undervalued). | Not present |
| **Judgment-compounding loop** | The vision's actual goal: *"the product is me becoming an exceptional entrepreneur."* Investment Committee + Decision Journal + backtesting make you a better investor every month. | Deferred to "ongoing/future" |

**Design rule for every phase below:** each phase must ship something that measurably improves *one investment decision* — not just "more data."

---

## 2. Reconciling the 14 vision modules with the build

`plan.md` collapsed 14 vision modules into 9 v1 modules. This roadmap keeps the 9-module spine and re-inserts the four wealth modules as their own track, so nothing in the vision is silently dropped.

| Vision (overall_plan.md) | Build track | Roadmap phase |
|---|---|---|
| M1 Knowledge Vault | Knowledge Vault (docs + notes + embeddings) | Phase 5 (+ OCR later) |
| M2 Relationship Intelligence | M5 Contacts & Memory | Phase 5 |
| M3 Property Intelligence | M1 Collector + M3 Tracker | Phase 1–2 |
| M4 Builder Intelligence | M4 (RERA-first) | Phase 4 |
| M5 Broker Intelligence | folded into M5 Contacts (trust score) | Phase 5 |
| M6 Market Intelligence | M8 Govt Watch + locality index | Phase 4–5 |
| M7 Property Discovery | M1 + off-market ingestion | Phase 1, 5 |
| M8 Opportunity Engine | M7 Deal Score + distress detection | Phase 2 |
| M9 Site Visit Assistant | Site Visit Assistant | Phase 6 |
| **M10 Investment Committee** | **Judgment track** | **Phase 3 (pulled early)** |
| **M11 Decision Journal** | **Judgment track** | **Phase 3 (pulled early)** |
| M12 Portfolio Dashboard | Portfolio | Phase 6 |
| M13 Learning Engine | Learning | Phase 6 (seeded earlier) |
| M14 Daily Briefing | M9 Intelligence Report | Phase 2 |

---

## 3. Phased roadmap

Estimates are **effort-hours**, not calendar time (`plan.md` §6 convention). Each phase's **done-when gate** is what matters.

### Phase 0 — Graduate the trial to a real foundation (~12–18h)
The `trial/` spike proved Apify actors are reliable/cheap. Now build the durable spine.

- Postgres 16 + pgvector + Alembic; migrate [docs/schema.sql](docs/schema.sql) (it already covers most of the vision).
- Port the trial's Apify ingestion (MagicBricks) onto Postgres; keep raw-payload-first + health monitoring.
- Docker Compose (FastAPI + Postgres + Caddy HTTPS/auth), CI with golden-fixture parser tests, nightly backup + tested restore, healthchecks.io dead-man's switch.

**Done when:** schema migrated; MagicBricks flows raw → parsed → stored in Postgres; a deliberate parser break is caught by monitoring and re-parsed from raw. *(This is `plan.md` Phase 0+1 merged, reusing the trial code.)*

### Phase 1 — Data spine **+ legal guardrail** (~35–45h)
Front-load the "don't lose money" layer before you trust any recommendation.

- M1 Collector for MagicBricks **+ Karnataka RERA ingestion** (RERA is `plan.md`'s own Priority 1 source — pull it in now, it's free, public, structured).
- Change tracking: new / updated / price-changed / removed / relisted (relist routed through entity matching so days-on-market isn't silently reset).
- **NEW — Bangalore Legal-Risk Layer v1** (see Appendix A). Every listing gets tagged, with cited source, on: khata type (A/B/E/e-khata), jurisdiction (BBMP/BDA/BMRDA/panchayat), RERA-registered yes/no, approved-layout vs revenue-site, DC-conversion status for plots. Crude-but-cited beats absent.
- Guidance-value baselines per locality (the arbitrage yardstick).

**Done when:** 7 consecutive clean ingestion days; every stored listing carries a legal-risk tag + source; RERA project data joins to listings by promoter/project.

### Phase 2 — The product ships: the Daily Briefing (~30–40h)
- **Deal Score v1** — transparent weighted factors with stored decomposition and versioned weights. Factors are Bangalore-real, not generic: legal-risk penalty, infra-proximity bonus, guidance-value gap, distress (price-drop depth/repetition + days-on-market), liquidity, rental-yield, value-creation upside.
- Haiku (Batch API) reads *descriptions selectively* for distress keywords **and seller-motivation** ("why is this available / why selling" — the vision's core question), with typed Pydantic output + citations.
- **NEW — capital-aware ranking:** filter/rank by affordability and financeability (your budget + LTV), and bias toward value-creation plays over buy-and-hold.
- Daily email digest (Resend/SES): top opportunities with cited evidence, price changes, new listings, source-health line. Strict thresholds; quiet days allowed. 👍/👎 per recommendation.

**Done when:** the briefing arrives every morning for 14 days; ≥1 recommendation/week is genuinely worth reading; every recommendation shows legal flags + evidence + why-selling.

### Phase 2b — Capital Plan: how the first deal actually gets funded (~15–20h)

**Why this exists:** the roadmap assumed the reader could transact. In practice
the capital is in equity, the emergency fund is inside it, and *stamp duty and
registration cannot be borrowed* — so there is a hard cash floor (~₹15.3L for a
₹44L plot) below which no amount of ranking matters. A briefing that shows
un-buyable properties every morning trains you to ignore it.

Until the floor is cleared, the briefing's job is **not** "buy this." It is
"here is your countdown, here is what your corridor is doing, here is who to
call." Same data, different product.

- **Reserve-first capital model** (built — `atlas/profile.py`, `profile-v2`):
  deployable = liquid − reserve. The emergency fund is never buying power;
  spending it is how an owner becomes the forced seller this system is built to
  *find in others*. `reserve_shortfall_for_emi()` warns that a secured EMI
  *raises* the reserve requirement — before committing, not after.
- **Runway** (built): `months_until_affordable(price, annual_appreciation)`
  returns `None` when the market outruns savings. That `None` is the point —
  it means "buy smaller or further out **now**", and a system that only ever
  said "keep saving" would hide it.
- **To build:** liquidation cost (LTCG on the funding sale is part of the true
  price of the plot); loan *eligibility* as distinct from LTV (banks size on
  income and existing EMIs, and plot loans are stricter than home loans);
  "am I saving faster than my corridor is appreciating?" as a tracked metric
  once locality ₹/sqft history exists.
- **Later, once assets exist:** capital recycling — sale vs loan-against-
  property vs top-up, and rental income raising serviceability for the *next*
  purchase. This is the "sell this to buy that" sequencing that M12 Portfolio
  only tracks and never plans.

**Done when:** the briefing states the capital it assumed, the cash floor, and
the countdown — and never surfaces a property that cannot be funded on the date
it is shown.

### Phase 3 — The judgment layer (the 9/10 differentiator, pulled early) (~25–35h)
Mostly prompts + schemas + storage — cheap, and it compounds immediately, even on deals you find manually offline.

- **M10 Investment Committee** — pick a property (from briefing or paste a URL) → structured **bull/bear memo**: why buy / why not, worst case / best case, exit strategy, capital needed (with financing + Karnataka stamp duty/registration), holding period, legal risks, rental potential, alternatives you passed on. Atlas argues *both* sides.
- **M11 Decision Journal** — every serious decision recorded. *Before:* thesis, expected return, confidence, risks. *After:* actual outcome, lesson, mistake. This is the raw material for backtesting and bias detection.
- M6 Property Analyzer (URL in → structured analysis out) folds into the committee flow.

**Done when:** every property you seriously consider has a committee memo + journal entry; you can pull up your last N decisions and their current status in one view.

### Phase 4 — Intelligence depth (~50–70h)
- **M2 Entity Resolution** across a 2nd/3rd portal per [docs/entity-resolution.md](docs/entity-resolution.md) (dedup; protect the days-on-market distress signal). Precision gate ≥95% on auto-merges.
- **M4 Builder Intelligence** — RERA-first: registrations, declared vs actual completion, quarterly progress, complaints/litigation, delay history; news summaries with citations; financial-stress signals. Answers *"Would I trust this builder?"*
- Locality micro-market index (₹/sqft trend, inventory, absorption per area).
- **Infrastructure-appreciation model (PostGIS, pulled forward from "future"):** distance to operational vs under-construction vs approved metro stations, PRR/STRR, IT hubs, airport corridor — with a *time-to-completion decay* so an approved-but-distant line is weighted below an opening-soon one. **Buffer-zone / rajakaluve / lake geofences as a RISK layer** (NGT demolition exposure).

**Done when:** cross-portal duplicates merge at ≥95% precision on a labelled sample; builder memos appear in analyses; scores reflect map-based infra proximity and buffer-zone risk.

### Phase 5 — Relationship & off-market alpha (~30–40h)
Where the best Bangalore deals actually live.

- **M5 Contacts & Memory** — 10-second phone capture (a note or it won't be used), embeddings + retrieval, trust score, follow-up nudges surfaced in the digest.
- **Gmail + WhatsApp ingestion as a first-class SOURCE** — broker messages/forwards become tracked listings and off-market signals that never hit portals. Off-market deals flow into the same scoring + committee pipeline.
- **M1/M2 Knowledge Vault** — store notes, PDFs, brochures, meeting summaries; everything searchable via embeddings.
- **M8 Government Watch** — weekly BBMP/BDA/BMRDA/Metro notification digest, each item answering *"what opportunity does this create?"*

**Done when:** a note captured on the phone in <15s surfaces in the relevant property's analysis and the digest; a broker WhatsApp forward becomes a tracked, scored opportunity.

### Phase 6 — Learning, portfolio & the compounding loop (ongoing)
- **M13 Learning Engine** — one concept/week (khata types, EC, DC conversion, JD area-sharing ratios, FSI/FAR, TDR, guidance value, Karnataka taxation, negotiation). Turns you into the expert the vision describes. *(Seed a lightweight version as early as Phase 2 — it's cheap.)*
- **Backtesting harness** — replay historical listings against candidate scoring weights; tune thresholds from 👍/👎 + Decision Journal outcomes; surface recurring biases.
- **M12 Portfolio Dashboard** — once you own assets: cash flow, loans/leverage, rental income, equity, net worth, goal progress.
- **M9 Site Visit Assistant** — pre-visit checklist (Appendix A items), capture (photos/GPS/notes; voice later), post-visit summary with risks, questions, negotiation ideas, comparables, missing info.

### Continuous / future
OCR for EC / RTC / khata / survey-sketch parsing; voice notes (Whisper); JD feasibility calculator (area-sharing + FSI); construction-cost estimator; rental estimator; CDP/RMP zoning + satellite overlays; builder financial-health monitor. *(These stay future — but the schema and entity graph should not preclude them.)*

---

## 4. Where I'm strengthening `plan.md` (direct answer)

`plan.md` is excellent on reliability engineering. The gaps that would keep it below 9/10:

1. **Don't defer the judgment loop.** Investment Committee + Decision Journal are the *stated purpose* of the whole project ("become an exceptional entrepreneur"), yet `plan.md` pushes them to "Phase 5 / ongoing." They're cheap (prompts + storage) and compound from day one. → **Pulled to Phase 3.**
2. **Elevate legal-risk to Phase 1, as a scored dimension, not a Phase-3 checklist.** In Bangalore, avoiding one catastrophic title/khata/buffer-zone mistake outvalues years of alert accuracy. → **Appendix A, Phase 1.**
3. **Model infrastructure appreciation actively, not as a passive weekly digest.** Metro/PRR proximity *with time-to-completion decay* is the primary appreciation driver; make it a scoring factor with PostGIS earlier than "future." → **Phase 4.**
4. **Treat relationships/off-market (Gmail/WhatsApp) as a data SOURCE.** The best deals never hit portals; a CRM that's only manual capture misses the alpha. → **Phase 5, first-class ingestion.**
5. **Make ranking capital-aware and value-creation-biased.** You have limited capital; rank by affordability/financeability and value-creation upside, not raw score. → **Phase 2.**
6. **Encode seller-motivation reasoning** ("why is this available / why selling") — the vision's core question, currently absent from the numeric score. → **Phase 2 LLM extraction + Phase 3 committee narrative.**
7. **Make "capital needed / returns" financially real** — leverage/LTV/EMI, Karnataka stamp duty (~5–6%) + registration, capital-gains — so the committee's numbers can be trusted. → **Phase 3.**
8. **Guidance-value as an active arbitrage signal**, not just a locality baseline. → **Phase 1–2.**

---

## Appendix A — Bangalore intelligence to encode (the domain moat)

These are the signals a seasoned Bangalore investor checks by reflex. Encode them as **status to ingest per property**, not hard-coded facts (statuses change — pull current state, cite the source, timestamp it).

**Legal / title (catastrophe-avoidance — highest weight):**
- **Khata type** — A-khata (legal, loan-eligible, easy resale) vs B-khata (B-register, restricted loans/resale) vs E-khata / e-Aasthi (current Karnataka digitisation). Flag non-A explicitly.
- **Jurisdiction** — BBMP vs BDA vs BMRDA vs gram panchayat (drives approvals, tax, resale liquidity).
- **Approved layout vs revenue site** — BDA/BMRDA-approved vs unapproved "revenue" layout (cheap, high-risk).
- **DC conversion** — agricultural → non-agricultural, essential for plots.
- **RERA registration** — cross-check the project against Karnataka RERA.
- **Encumbrance Certificate (EC)** status + litigation/court-dispute flags (semi-manual per-property pull at diligence — Kaveri is OTP-gated, no bulk API, per `plan.md` §4).
- **Buffer-zone exposure** — rajakaluve (storm-water drain) / lake buffers → NGT demolition risk. Geofence as a hard risk in Phase 4.

**Appreciation drivers (upside modelling):**
- Metro proximity by **completion stage** (operational Purple/Green > under-construction Yellow/Blue/Pink > approved Phase 3), with time-decay.
- PRR / STRR / airport corridor / IT-hub proximity.
- Guidance-value gap (listing ₹/sqft vs govt guidance value) — arbitrage signal.
- Zoning / master-plan land use (residential / commercial / green-belt / buffer).

**Deal / distress signals:**
- Price-drop depth + repetition; days-on-market (protected by entity resolution across relists).
- Seller motivation from description/broker text (relocation, quick sale, NRI seller, distress).
- Builder unsold-inventory / financial-stress (Phase 4).
- Value-creation flags: JD potential (plot size + zoning + road width), redevelopment/TDR, commercial-conversion, corner site, road-widening.

**Financing / returns realism:**
- Loan eligibility & LTV (khata-dependent), EMI, down-payment vs your capital.
- Karnataka stamp duty (~5% + cess/surcharge) + 1% registration; GST on under-construction.
- Rental yield (Bangalore ~3–4% — appreciation, not yield, is the game).

> **Verification note:** metro-line stages, RMP/CDP zoning status, and scheme legality (e.g. Akrama Sakrama) change over time and are past my training cutoff. The system must *pull* current status and cite it, never assume — which is the correct engineering stance regardless.
