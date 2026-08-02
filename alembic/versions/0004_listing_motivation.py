"""Cached seller-motivation extractions (the 'why-selling' factor).

atlas_roadmap Phase 2's done-when requires every recommendation to show
"legal flags + evidence + why-selling", and overall_plan.md calls *why is this
available* the core question. That answer lives in the listing's prose, so it
needs an LLM read — which makes it the first thing in Atlas that costs money
per row and cannot be recomputed for free.

Hence a cache table rather than an inline call:

- **The Batch API is asynchronous** (minutes to 24h), so extraction cannot
  block the 07:00 scoring job. Submission and collection are separate passes;
  a listing with no result yet simply makes the factor abstain, which is the
  same behaviour as any other missing datum.
- **Keyed on (listing_id, prompt_version)** so re-running never re-bills, and
  `source_hash` records WHICH text produced the answer — an edited description
  invalidates the row instead of silently keeping a stale reading.
- `status` distinguishes pending / ok / refused / invalid / empty, because
  "the model declined" and "we haven't asked yet" must not look alike. Only
  `ok` feeds the score.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-02
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE listing_motivation (
    id             bigserial PRIMARY KEY,
    listing_id     bigint NOT NULL REFERENCES listings(id),
    prompt_version text NOT NULL,        -- plan §7: version everything that judges
    model          text NOT NULL,        -- exact model id that produced this
    source_hash    text NOT NULL,        -- sha256 of the analysed text
    status         text NOT NULL,        -- pending | ok | refused | invalid | empty
    motivated      boolean,
    score          numeric,              -- 0-1, derived in code from the signals
    signals        text[] NOT NULL DEFAULT '{}',
    quote          text,                 -- verbatim, so the claim is checkable
    confidence     numeric,
    batch_id       text,                 -- Anthropic batch id, for audit
    requested_at   timestamptz NOT NULL DEFAULT now(),
    extracted_at   timestamptz,
    UNIQUE (listing_id, prompt_version)
);
-- The collection pass finds work by batch; the scorer reads by listing.
CREATE INDEX ON listing_motivation (status, batch_id);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS listing_motivation;")
