"""Deal Score v1 — scores attach to listings, not yet to properties.

The designed `scores` and `recommendations` tables are property-scoped, because
the vision resolves one property from many portal listings. But entity
resolution is Phase 4 and `properties` is EMPTY, so a property-scoped score
cannot be written today.

The alternative — minting a degenerate `properties` row per listing — was
rejected: it pre-empts the Phase-4 merge semantics with rows nothing produced,
and entity resolution would then have to un-invent them. Instead the subject
becomes nullable on both sides with a CHECK that exactly one is set, so a score
written today against a listing and a score written in Phase 4 against a
resolved property are the same table with an explicit, self-documenting
discriminator.

Also lands three things Deal Score v1 depends on:

- `listings.posted_at` — the PORTAL's own posting date. Both feeds already
  carry it and it has been discarded until now, so days-on-market could only
  ever mean "days since Atlas first noticed", which is bounded below by the
  first collection day and reads every listing as brand new. Backfillable from
  `raw_payloads` (that is what raw-first is for).
- `scores.score_date` (Asia/Kolkata) + a unique index — makes scoring
  append-only ACROSS days but idempotent WITHIN one. Re-running today replaces
  today's row; yesterday's is immutable, so `recommendations.score_id` on an
  email already delivered still points at the number that was actually sent.
- `report_runs.sent_at` — the double-send guard. `report_date` being unique
  stops a duplicate row, not a duplicate email: a container restart after the
  digest job would otherwise re-send.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-02
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
-- The portal's own posting date, for real days-on-market.
ALTER TABLE listings ADD COLUMN posted_at timestamptz;
CREATE INDEX ON listings (posted_at);

-- Scores: listing-scoped now, property-scoped from Phase 4.
ALTER TABLE scores ADD COLUMN listing_id bigint REFERENCES listings(id);
ALTER TABLE scores ALTER COLUMN property_id DROP NOT NULL;
ALTER TABLE scores ADD CONSTRAINT scores_subject_ck
    CHECK (num_nonnulls(listing_id, property_id) = 1);

-- The IST day this score is for. Not derived from computed_at: the timezone
-- conversion is not immutable, so it could not be indexed, and the daily job
-- must be idempotent on a key Postgres can enforce.
ALTER TABLE scores ADD COLUMN score_date date NOT NULL DEFAULT current_date;

-- Fraction of non-zero weight that actually produced a value. A factor with no
-- data for this listing ABSTAINS rather than scoring zero, and `overall` is
-- renormalised over what was covered — so a thin locality yields a lower
-- coverage, not a silently depressed score.
ALTER TABLE scores ADD COLUMN coverage numeric NOT NULL DEFAULT 1.0;

CREATE UNIQUE INDEX scores_listing_day_uq
    ON scores (listing_id, weights_version, score_date);
CREATE INDEX scores_listing_recent_idx ON scores (listing_id, computed_at DESC);

-- Recommendations follow the same subject rule. Left nullable on both columns
-- rather than CHECK-constrained: an instant-tier alert may reference neither
-- yet, and tightening this is a Phase-4 concern once properties exist.
ALTER TABLE recommendations ADD COLUMN listing_id bigint REFERENCES listings(id);
CREATE INDEX ON recommendations (listing_id, sent_at);

-- Null until the digest is actually delivered; stamped on success so a restart
-- inside the send window cannot re-send.
ALTER TABLE report_runs ADD COLUMN sent_at timestamptz;
""")


def downgrade() -> None:
    op.execute("""
ALTER TABLE report_runs DROP COLUMN IF EXISTS sent_at;
DROP INDEX IF EXISTS recommendations_listing_id_sent_at_idx;
ALTER TABLE recommendations DROP COLUMN IF EXISTS listing_id;
DROP INDEX IF EXISTS scores_listing_recent_idx;
DROP INDEX IF EXISTS scores_listing_day_uq;
ALTER TABLE scores DROP COLUMN IF EXISTS coverage;
ALTER TABLE scores DROP COLUMN IF EXISTS score_date;
-- property_id NOT NULL is only restorable once listing-scoped rows are gone,
-- and the CHECK must go before the column it references.
ALTER TABLE scores DROP CONSTRAINT IF EXISTS scores_subject_ck;
DELETE FROM score_factors WHERE score_id IN
    (SELECT id FROM scores WHERE property_id IS NULL);
DELETE FROM scores WHERE property_id IS NULL;
ALTER TABLE scores DROP COLUMN IF EXISTS listing_id;
ALTER TABLE scores ALTER COLUMN property_id SET NOT NULL;
DROP INDEX IF EXISTS listings_posted_at_idx;
ALTER TABLE listings DROP COLUMN IF EXISTS posted_at;
""")
