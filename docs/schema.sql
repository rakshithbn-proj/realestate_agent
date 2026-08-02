-- Real Estate Intelligence Agent — core schema (PostgreSQL 16)
-- Referenced by plan.md §6 Phase 0. Managed via Alembic in the real project;
-- this file is the design source of truth for table shapes and relationships.
-- Migrated as alembic/versions/0001_core_schema.py (2026-07-24), with:
--   * multi-city columns (handoff §4a): sources.city, localities.city,
--     listings.city; uniqueness city-scoped
--   * listings.rera_ids text[] — canonical PRM/KA/RERA ids for the 99.6%
--     registry join (handoff §7)
--   * EMBEDDINGS DEFERRED (handoff §9.4 option B): the vector extension and
--     the vector(N) columns below are NOT in migration 0001. They land in the
--     Phase-3 semantic-search migration, pinned to the embedding model chosen
--     then (assumed default voyage-3.5 → vector(1024)). Nothing writes
--     embeddings before that, so the later ALTER is metadata-only — and
--     Phase 0/1 runs on stock Postgres 16 (pg_trgm is bundled; pgvector isn't).
-- Later migrations folded in below:
--   * 0002 — listing_legal_tags (per-listing claims, never conflated with the
--     property-scoped, document-verified legal_checks)
--   * 0003 — Deal Score v1: listings.posted_at; scores/recommendations become
--     listing-scoped-or-property-scoped; scores.score_date + coverage;
--     report_runs.sent_at

CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram indexes for fuzzy name matching
-- CREATE EXTENSION vector;                 -- pgvector: Phase-3 migration (see header)

-- ============================================================
-- Ingestion spine (plan §7: raw first, parse second)
-- ============================================================

CREATE TABLE sources (
    id            serial PRIMARY KEY,
    name          text NOT NULL,                 -- 'magicbricks', 'nobroker', 'rera_karnataka', ...
    city          text NOT NULL DEFAULT 'bangalore',  -- market slug (handoff §4a)
    kind          text NOT NULL,                 -- 'portal' | 'official' | 'news'
    fetcher       text NOT NULL,                 -- 'apify:thirdwatch/magicbricks-scraper' | 'custom'
    expected_daily_volume int,                   -- health monitoring baseline
    enabled       boolean NOT NULL DEFAULT true,
    UNIQUE (name, city)
);

CREATE TABLE scrape_runs (
    id            bigserial PRIMARY KEY,
    source_id     int NOT NULL REFERENCES sources(id),
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running',  -- running | ok | failed | anomalous
    items_found   int,
    error         text
);

-- Raw payloads: every scrape stores its raw HTML/JSON before parsing.
-- Parsers can break and be re-run; data loss is unacceptable.
CREATE TABLE raw_payloads (
    id            bigserial PRIMARY KEY,
    scrape_run_id bigint NOT NULL REFERENCES scrape_runs(id),
    external_id   text,                          -- portal's listing id when known
    url           text,
    payload       jsonb,                         -- structured actor output (Apify)
    payload_text  text,                          -- raw HTML/text for custom scrapers (RERA, notifications)
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (payload IS NOT NULL OR payload_text IS NOT NULL)
);
CREATE INDEX ON raw_payloads (scrape_run_id);
CREATE INDEX ON raw_payloads (external_id);

-- ============================================================
-- Listings (per-portal) and versions (change tracking, M1)
-- ============================================================

CREATE TABLE listings (
    id             bigserial PRIMARY KEY,
    source_id      int NOT NULL REFERENCES sources(id),
    external_id    text NOT NULL,                -- portal listing id
    property_id    bigint,                       -- FK -> properties, set by entity resolution (soft link)
    url            text,
    status         text NOT NULL DEFAULT 'active',  -- active | removed | relisted
    -- normalized fields (docs/entity-resolution.md stage 1)
    title          text,
    project_raw    text,
    project_norm   text,
    address_raw    text,
    locality_id    int,                          -- FK -> localities
    city           text,                         -- market slug, denormalised for pre-resolution segmentation
    lat            double precision,
    lon            double precision,
    geohash6       text,
    property_type  text,                         -- apartment | plot | villa | commercial
    bhk            smallint,
    floor          smallint,
    area_sqft      numeric,
    price_inr      bigint,
    price_per_sqft numeric GENERATED ALWAYS AS (CASE WHEN area_sqft > 0 THEN price_inr / area_sqft END) STORED,
    lister_kind    text,                         -- owner | broker | builder
    lister_phone   text,
    builder_id     int,                          -- FK -> builders
    rera_ids       text[] NOT NULL DEFAULT '{}', -- canonical PRM/KA/RERA/... ids (99.6% registry join, handoff §7)
    description    text,
    -- description_emb vector(1024),             -- DEFERRED to Phase-3 migration (see header)
    image_hashes   text[],                       -- pHash per photo, for entity resolution
    parser_version text NOT NULL,                -- plan §7: version everything that judges
    -- The PORTAL's own posting date. Distinct from first_seen_at, which is only
    -- "when Atlas noticed" — using that for days-on-market makes every listing
    -- look new until Atlas has been running for months.
    posted_at      timestamptz,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    removed_at     timestamptz,
    UNIQUE (source_id, external_id)
);
CREATE INDEX ON listings (property_id);
CREATE INDEX ON listings (posted_at);
CREATE INDEX ON listings (geohash6, bhk, property_type);   -- blocking key 2
CREATE INDEX ON listings (lister_phone) WHERE lister_phone IS NOT NULL;  -- blocking key 3
CREATE INDEX ON listings USING gin (project_norm gin_trgm_ops);          -- blocking key 1
CREATE INDEX ON listings (locality_id, status);
CREATE INDEX ON listings (status, removed_at);             -- relist matching against recent removals
CREATE INDEX ON listings USING gin (rera_ids);             -- registry join

-- Immutable snapshot on every observed change (new/updated/price/removed/relisted)
CREATE TABLE listing_versions (
    id            bigserial PRIMARY KEY,
    listing_id    bigint NOT NULL REFERENCES listings(id),
    scrape_run_id bigint REFERENCES scrape_runs(id),
    change_kind   text NOT NULL,                 -- new | updated | price_changed | removed | relisted
    snapshot      jsonb NOT NULL,                -- normalized fields at this observation
    observed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON listing_versions (listing_id, observed_at);

-- Price history distilled for the tracker (M3)
CREATE TABLE price_events (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id),
    old_price    bigint,
    new_price    bigint NOT NULL,
    pct_change   numeric,
    observed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON price_events (listing_id, observed_at);

-- ============================================================
-- Resolved entities (M2 — docs/entity-resolution.md)
-- ============================================================

CREATE TABLE properties (
    id             bigserial PRIMARY KEY,
    canonical_listing_id bigint,                 -- best listing to display
    confidence     numeric NOT NULL DEFAULT 1.0,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE entity_merge_log (
    id            bigserial PRIMARY KEY,
    property_id   bigint NOT NULL REFERENCES properties(id),
    listing_id    bigint NOT NULL REFERENCES listings(id),
    action        text NOT NULL,                 -- merge | split
    score         numeric,
    features      jsonb,                         -- per-feature score breakdown (evidence)
    actor         text NOT NULL,                 -- 'auto' | 'human'
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON entity_merge_log (property_id);
CREATE INDEX ON entity_merge_log (listing_id);

-- ============================================================
-- Scoring (M7): every score decomposes into cited factors
-- ============================================================

CREATE TABLE score_weights (
    version      int PRIMARY KEY,
    weights      jsonb NOT NULL,                 -- {factor: weight}
    created_at   timestamptz NOT NULL DEFAULT now(),
    note         text
);

-- A score judges exactly ONE subject. Listing-scoped today (migration 0003);
-- property-scoped once entity resolution exists in Phase 4. Minting a
-- degenerate properties row per listing was rejected — it would pre-empt the
-- Phase-4 merge semantics with rows nothing produced.
CREATE TABLE scores (
    id              bigserial PRIMARY KEY,
    listing_id      bigint REFERENCES listings(id),
    property_id     bigint REFERENCES properties(id),
    weights_version int NOT NULL REFERENCES score_weights(version),
    overall         numeric NOT NULL,            -- 0-100
    -- Fraction of non-zero weight that produced a value. Factors with no data
    -- for this listing ABSTAIN and `overall` is renormalised over what was
    -- covered, so a thin locality lowers coverage rather than the score.
    coverage        numeric NOT NULL DEFAULT 1.0,
    -- The Asia/Kolkata day this score is for: append-only across days,
    -- idempotent within one, so a delivered recommendation's score never moves.
    score_date      date NOT NULL DEFAULT current_date,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(listing_id, property_id) = 1)
);
CREATE UNIQUE INDEX ON scores (listing_id, weights_version, score_date);
CREATE INDEX ON scores (listing_id, computed_at DESC);

CREATE TABLE score_factors (
    id           bigserial PRIMARY KEY,
    score_id     bigint NOT NULL REFERENCES scores(id),
    factor       text NOT NULL,                  -- 'price_reduction', 'days_on_market', ...
    value        numeric NOT NULL,
    evidence     jsonb NOT NULL                  -- {table, row_id, detail} — plan §2 principle 1
);
CREATE INDEX ON score_factors (score_id);

-- ============================================================
-- Builders & RERA (M4)
-- ============================================================

CREATE TABLE builders (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    name_norm    text NOT NULL UNIQUE,
    reputation_summary text,                     -- LLM summary, always with citations in summary_sources
    summary_sources jsonb,
    updated_at   timestamptz
);

CREATE TABLE rera_projects (
    id             bigserial PRIMARY KEY,
    rera_reg_no    text NOT NULL UNIQUE,
    builder_id     int REFERENCES builders(id),
    project_name   text,
    district       text,
    declared_completion date,
    status         text,
    complaints_count int,
    raw            jsonb NOT NULL,               -- full scraped record
    fetched_at     timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- Localities & micro-market index (M3)
-- ============================================================

CREATE TABLE localities (
    id        serial PRIMARY KEY,
    name      text NOT NULL,
    city      text NOT NULL DEFAULT 'bangalore',  -- market slug (handoff §4a)
    aliases   text[] NOT NULL DEFAULT '{}',
    zone      text,                               -- e.g. 'east', 'north' (per-city directional)
    UNIQUE (city, name)
);

CREATE TABLE locality_metrics (
    locality_id     int NOT NULL REFERENCES localities(id),
    as_of           date NOT NULL,
    active_listings int,
    median_price_per_sqft numeric,
    median_days_on_market numeric,
    price_drop_share numeric,                    -- share of listings with a reduction in last 30d
    PRIMARY KEY (locality_id, as_of)
);

-- ============================================================
-- Contacts & memory (M5): CRM + embedded interaction notes
-- ============================================================

CREATE TABLE contacts (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    phone        text UNIQUE,
    kind         text NOT NULL DEFAULT 'broker', -- broker | owner | builder_rep
    areas        text[],
    trust_score  smallint,
    last_contact_at timestamptz,
    notes        text
);

CREATE TABLE interactions (
    id           bigserial PRIMARY KEY,
    contact_id   int REFERENCES contacts(id),
    property_id  bigint REFERENCES properties(id),
    channel      text,                            -- call | whatsapp | visit | note
    content      text NOT NULL,
    -- content_emb vector(1024),                 -- DEFERRED to Phase-3 migration (see header)
    follow_up_at date,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON interactions (contact_id, created_at);

-- ============================================================
-- Outcomes & feedback (M7 learning loop, M9 report)
-- ============================================================

CREATE TABLE outcomes (
    id           bigserial PRIMARY KEY,
    property_id  bigint NOT NULL REFERENCES properties(id),
    kind         text NOT NULL,                  -- sold | withdrawn | visited_passed | negotiated | bought
    detail       jsonb,                          -- e.g. {"final_price": ..., "days_on_market": ...}
    recorded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE report_runs (
    id           bigserial PRIMARY KEY,
    report_date  date NOT NULL UNIQUE,
    generated_at timestamptz NOT NULL DEFAULT now(),
    content      jsonb NOT NULL,                 -- structured report; rendered to email/html
    source_health jsonb NOT NULL,                -- per-source status included in every report
    -- Double-send guard. UNIQUE(report_date) prevents a duplicate ROW, not a
    -- duplicate EMAIL: a restart after the digest job would otherwise re-send.
    sent_at      timestamptz                     -- null until actually delivered
);

CREATE TABLE recommendations (
    id           bigserial PRIMARY KEY,
    -- NULL for instant-tier alerts (fired outside a daily run); set for daily digest items
    report_run_id bigint REFERENCES report_runs(id),
    listing_id   bigint REFERENCES listings(id),   -- subject today (migration 0003)
    property_id  bigint REFERENCES properties(id), -- subject from Phase 4
    tier         text NOT NULL,                  -- instant | daily
    headline     text NOT NULL,
    score_id     bigint REFERENCES scores(id),
    sent_at      timestamptz NOT NULL DEFAULT now(),
    feedback     smallint                        -- +1 / -1 from the user, null = no feedback
);
CREATE INDEX ON recommendations (property_id, sent_at);
CREATE INDEX ON recommendations (listing_id, sent_at);

-- ============================================================
-- Legal screening & documents (M6 checklist, per-property EC pulls)
-- ============================================================

CREATE TABLE documents (
    id           bigserial PRIMARY KEY,
    property_id  bigint NOT NULL REFERENCES properties(id),
    kind         text NOT NULL,                  -- ec | khata | sale_deed | rera_cert | other
    file_path    text NOT NULL,                  -- local/blob storage path
    parsed       jsonb,                          -- extracted fields (OCR/LLM output, with parser_version)
    fetched_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON documents (property_id);

-- Property-scoped, document-VERIFIED checks at diligence time (Phase 3+).
-- Distinct from listing_legal_tags below, which are per-listing, crude, and
-- often only the seller's *claim* — the two must never be conflated.
CREATE TABLE legal_checks (
    id           bigserial PRIMARY KEY,
    property_id  bigint NOT NULL REFERENCES properties(id),
    item         text NOT NULL,                  -- khata_type | ec_clean | rera_registered | litigation | title_chain
    status       text NOT NULL,                  -- pass | flag | fail | unknown
    detail       text,
    evidence     jsonb,                          -- {document_id | rera_project_id | url}
    checked_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (property_id, item)
);

-- Per-LISTING legal-risk tags (Phase 1 — migration 0002, atlas_roadmap App. A).
-- Added beyond the original design: the guardrail layer must tag every listing
-- as it arrives, before entity resolution exists. rera_registered is a
-- verifiable registry join; khata/jurisdiction/layout are listing-text CLAIMS
-- (evidence.kind = 'listing_text_claim', explicitly not document-verified).
CREATE TABLE listing_legal_tags (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id),
    item         text NOT NULL,                  -- rera_registered | khata_type | jurisdiction | layout_approval
    status       text NOT NULL,                  -- pass | flag | fail | unknown
    detail       text,
    evidence     jsonb,                          -- cited: text snippet, rera_project id, ...
    tagger_version text NOT NULL,                -- plan §7: version everything that judges
    checked_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (listing_id, item)
);
CREATE INDEX ON listing_legal_tags (item, status);
