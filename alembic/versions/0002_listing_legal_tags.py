"""Listing-level legal-risk tags (Phase 1 — atlas_roadmap Appendix A).

The designed `legal_checks` table is property-scoped, for diligence-time
verification once entity resolution exists (Phase 4). Phase 1 needs the
crude-but-cited layer on every LISTING as it arrives — a separate table, so
listing-text claims ("A khata, BDA approved") are never conflated with
document-verified checks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE listing_legal_tags (
    id           bigserial PRIMARY KEY,
    listing_id   bigint NOT NULL REFERENCES listings(id),
    item         text NOT NULL,      -- rera_registered | khata_type | jurisdiction | layout_approval
    status       text NOT NULL,      -- pass | flag | fail | unknown
    detail       text,
    evidence     jsonb,              -- cited source: text snippet, rera_project id, ...
    tagger_version text NOT NULL,    -- plan §7: version everything that judges
    checked_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (listing_id, item)
);
CREATE INDEX ON listing_legal_tags (item, status);
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS listing_legal_tags;")
