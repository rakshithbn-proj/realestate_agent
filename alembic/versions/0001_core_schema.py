"""Core schema — docs/schema.sql migrated (Phase 0).

Deltas vs the original docs/schema.sql design (all reflected back into that
file, which remains the design source of truth):

1. Multi-city (handoff.md §4a / §5 step 3a): `city` on sources, localities and
   listings; uniqueness is city-scoped. Bangalore is the default market;
   Mysore becomes a data addition, not a migration.
2. `listings.rera_ids text[]`: canonical PRM/KA/RERA/... ids parsed from portal
   payloads — the 99.6% join to the RERA registry (handoff.md §7) needs a
   column to join on.
3. Embedding columns DEFERRED (handoff.md §9.4, option B): no vector columns
   and no `CREATE EXTENSION vector` here. They arrive in the Phase-3 migration
   that introduces semantic search, pinned to the embedding model actually
   chosen (assumed default: voyage-3.5, 1024-dim). Until then nothing writes
   embeddings, so adding the columns later is a metadata-only ALTER. This also
   lets Phase 0/1 run against stock Postgres 16 (pg_trgm is bundled; pgvector
   is not) — the compose image ships pgvector ready for when it's needed.

Revision ID: 0001
Revises:
Create Date: 2026-07-24
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram indexes for fuzzy name matching

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
    external_id   text,
    url           text,
    payload       jsonb,                         -- structured actor output (Apify)
    payload_text  text,                          -- raw HTML/text for custom scrapers
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (payload IS NOT NULL OR payload_text IS NOT NULL)
);
CREATE INDEX ON raw_payloads (scrape_run_id);
CREATE INDEX ON raw_payloads (external_id);

-- ============================================================
-- Localities & micro-market index (M3) — created before listings (FK target)
-- ============================================================

CREATE TABLE localities (
    id        serial PRIMARY KEY,
    name      text NOT NULL,
    city      text NOT NULL DEFAULT 'bangalore', -- market slug (handoff §4a)
    aliases   text[] NOT NULL DEFAULT '{}',
    zone      text,                              -- e.g. 'east', 'north' (per-city directional)
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
-- Builders & RERA (M4) — created before listings (FK target)
-- ============================================================

CREATE TABLE builders (
    id           serial PRIMARY KEY,
    name         text NOT NULL,
    name_norm    text NOT NULL UNIQUE,
    reputation_summary text,                     -- LLM summary, cited in summary_sources
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
-- Listings (per-portal) and versions (change tracking, M1)
-- ============================================================

CREATE TABLE listings (
    id             bigserial PRIMARY KEY,
    source_id      int NOT NULL REFERENCES sources(id),
    external_id    text NOT NULL,
    property_id    bigint,                       -- FK -> properties, set by entity resolution (soft link)
    url            text,
    status         text NOT NULL DEFAULT 'active',  -- active | removed | relisted
    title          text,
    project_raw    text,
    project_norm   text,
    address_raw    text,
    locality_id    int REFERENCES localities(id),
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
    builder_id     int REFERENCES builders(id),
    rera_ids       text[] NOT NULL DEFAULT '{}', -- canonical PRM/KA/RERA/... ids (99.6% registry join)
    description    text,
    -- description_emb vector(N): deferred to Phase-3 semantic-search migration
    image_hashes   text[],                       -- pHash per photo, for entity resolution
    parser_version text NOT NULL,                -- plan §7: version everything that judges
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    removed_at     timestamptz,
    UNIQUE (source_id, external_id)
);
CREATE INDEX ON listings (property_id);
CREATE INDEX ON listings (geohash6, bhk, property_type);   -- blocking key 2
CREATE INDEX ON listings (lister_phone) WHERE lister_phone IS NOT NULL;  -- blocking key 3
CREATE INDEX ON listings USING gin (project_norm gin_trgm_ops);          -- blocking key 1
CREATE INDEX ON listings (locality_id, status);
CREATE INDEX ON listings (status, removed_at);             -- relist matching against recent removals
CREATE INDEX ON listings USING gin (rera_ids);             -- registry join

CREATE TABLE listing_versions (
    id            bigserial PRIMARY KEY,
    listing_id    bigint NOT NULL REFERENCES listings(id),
    scrape_run_id bigint REFERENCES scrape_runs(id),
    change_kind   text NOT NULL,                 -- new | updated | price_changed | removed | relisted
    snapshot      jsonb NOT NULL,                -- normalized fields at this observation
    observed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON listing_versions (listing_id, observed_at);

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

CREATE TABLE scores (
    id              bigserial PRIMARY KEY,
    property_id     bigint NOT NULL REFERENCES properties(id),
    weights_version int NOT NULL REFERENCES score_weights(version),
    overall         numeric NOT NULL,            -- 0-100
    computed_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE score_factors (
    id           bigserial PRIMARY KEY,
    score_id     bigint NOT NULL REFERENCES scores(id),
    factor       text NOT NULL,                  -- 'price_reduction', 'days_on_market', ...
    value        numeric NOT NULL,
    evidence     jsonb NOT NULL                  -- {table, row_id, detail} — plan §2 principle 1
);
CREATE INDEX ON score_factors (score_id);

-- ============================================================
-- Contacts & memory (M5): CRM + interaction notes
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
    -- content_emb vector(N): deferred to Phase-3 semantic-search migration
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
    detail       jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE report_runs (
    id           bigserial PRIMARY KEY,
    report_date  date NOT NULL UNIQUE,
    generated_at timestamptz NOT NULL DEFAULT now(),
    content      jsonb NOT NULL,                 -- structured report; rendered to email/html
    source_health jsonb NOT NULL                 -- per-source status included in every report
);

CREATE TABLE recommendations (
    id           bigserial PRIMARY KEY,
    report_run_id bigint REFERENCES report_runs(id),  -- NULL for instant-tier alerts
    property_id  bigint REFERENCES properties(id),
    tier         text NOT NULL,                  -- instant | daily
    headline     text NOT NULL,
    score_id     bigint REFERENCES scores(id),
    sent_at      timestamptz NOT NULL DEFAULT now(),
    feedback     smallint                        -- +1 / -1 from the user, null = no feedback
);
CREATE INDEX ON recommendations (property_id, sent_at);

-- ============================================================
-- Legal screening & documents (M6 checklist, per-property EC pulls)
-- ============================================================

CREATE TABLE documents (
    id           bigserial PRIMARY KEY,
    property_id  bigint NOT NULL REFERENCES properties(id),
    kind         text NOT NULL,                  -- ec | khata | sale_deed | rera_cert | other
    file_path    text NOT NULL,
    parsed       jsonb,                          -- extracted fields (OCR/LLM output, with parser_version)
    fetched_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON documents (property_id);

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
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS legal_checks;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS recommendations;
DROP TABLE IF EXISTS report_runs;
DROP TABLE IF EXISTS outcomes;
DROP TABLE IF EXISTS interactions;
DROP TABLE IF EXISTS contacts;
DROP TABLE IF EXISTS score_factors;
DROP TABLE IF EXISTS scores;
DROP TABLE IF EXISTS score_weights;
DROP TABLE IF EXISTS entity_merge_log;
DROP TABLE IF EXISTS properties;
DROP TABLE IF EXISTS price_events;
DROP TABLE IF EXISTS listing_versions;
DROP TABLE IF EXISTS listings;
DROP TABLE IF EXISTS rera_projects;
DROP TABLE IF EXISTS builders;
DROP TABLE IF EXISTS locality_metrics;
DROP TABLE IF EXISTS localities;
DROP TABLE IF EXISTS raw_payloads;
DROP TABLE IF EXISTS scrape_runs;
DROP TABLE IF EXISTS sources;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
