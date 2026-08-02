"""ORM models for the ingestion spine and the scoring/reporting layer.

DDL is owned by Alembic migrations (see alembic/versions/), which were derived
from docs/schema.sql. Tables that no code touches yet (contacts, outcomes,
documents, legal_checks, ...) intentionally have no ORM class — they get one
when their module lands. These classes must stay in sync with the migrations
for the tables they map.
"""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("name", "city"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, default="bangalore")
    kind: Mapped[str] = mapped_column(Text)  # portal | official | news
    fetcher: Mapped[str] = mapped_column(Text)
    expected_daily_volume: Mapped[int | None]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="running")
    items_found: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)


class RawPayload(Base):
    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scrape_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"))
    external_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    payload_text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Locality(Base):
    __tablename__ = "localities"
    __table_args__ = (UniqueConstraint("city", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, default="bangalore")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    zone: Mapped[str | None] = mapped_column(Text)


class Builder(Base):
    __tablename__ = "builders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    name_norm: Mapped[str] = mapped_column(Text, unique=True)
    reputation_summary: Mapped[str | None] = mapped_column(Text)
    summary_sources: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReraProject(Base):
    __tablename__ = "rera_projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rera_reg_no: Mapped[str] = mapped_column(Text, unique=True)
    builder_id: Mapped[int | None] = mapped_column(ForeignKey("builders.id"))
    project_name: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    complaints_count: Mapped[int | None]
    raw: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(Text)
    property_id: Mapped[int | None] = mapped_column(BigInteger)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    title: Mapped[str | None] = mapped_column(Text)
    project_raw: Mapped[str | None] = mapped_column(Text)
    project_norm: Mapped[str | None] = mapped_column(Text)
    address_raw: Mapped[str | None] = mapped_column(Text)
    locality_id: Mapped[int | None] = mapped_column(ForeignKey("localities.id"))
    city: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    geohash6: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    bhk: Mapped[int | None] = mapped_column(SmallInteger)
    floor: Mapped[int | None] = mapped_column(SmallInteger)
    area_sqft: Mapped[float | None] = mapped_column(Numeric)
    price_inr: Mapped[int | None] = mapped_column(BigInteger)
    price_per_sqft: Mapped[float | None] = mapped_column(
        Numeric,
        Computed("CASE WHEN area_sqft > 0 THEN price_inr / area_sqft END"),
    )
    lister_kind: Mapped[str | None] = mapped_column(Text)
    lister_phone: Mapped[str | None] = mapped_column(Text)
    builder_id: Mapped[int | None]
    rera_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    description: Mapped[str | None] = mapped_column(Text)
    image_hashes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    parser_version: Mapped[str] = mapped_column(Text)
    # The portal's own posting date, when it publishes one. NOT first_seen_at:
    # that is only "when Atlas noticed", so using it for days-on-market reads
    # every listing as brand new until Atlas has been collecting for months.
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ListingLegalTag(Base):
    __tablename__ = "listing_legal_tags"
    __table_args__ = (UniqueConstraint("listing_id", "item"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    item: Mapped[str] = mapped_column(Text)    # rera_registered | khata_type | jurisdiction | layout_approval
    status: Mapped[str] = mapped_column(Text)  # pass | flag | fail | unknown
    detail: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    tagger_version: Mapped[str] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingVersion(Base):
    __tablename__ = "listing_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    scrape_run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id"))
    change_kind: Mapped[str] = mapped_column(Text)  # new | updated | price_changed | removed | relisted
    snapshot: Mapped[dict] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceEvent(Base):
    __tablename__ = "price_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    old_price: Mapped[int | None] = mapped_column(BigInteger)
    new_price: Mapped[int] = mapped_column(BigInteger)
    pct_change: Mapped[float | None] = mapped_column(Numeric)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingMotivation(Base):
    """Cached 'why is this being sold' extraction for one listing.

    Separate from the score because it is asynchronous and billed: the Batch
    API answers in minutes to hours, so extraction cannot run inline with
    scoring. A listing with no `ok` row here makes the seller_motivation factor
    abstain — the same as any other missing datum.
    """

    __tablename__ = "listing_motivation"
    __table_args__ = (UniqueConstraint("listing_id", "prompt_version"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    prompt_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    # sha256 of the analysed text: an edited description invalidates the row
    # instead of leaving a stale reading attached to new prose.
    source_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # pending|ok|refused|invalid|empty
    motivated: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Numeric)
    signals: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    quote: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Numeric)
    batch_id: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoreWeightSet(Base):
    """One immutable row per weights version — the versioned judgement.

    `version` is the primary key on purpose: weights are never edited in place.
    Changing how deals rank means a new version, so every stored score stays
    attributable to the exact weights that produced it (plan §7).
    """

    __tablename__ = "score_weights"

    version: Mapped[int] = mapped_column(primary_key=True)
    weights: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text)


class Score(Base):
    """A Deal Score for exactly one subject.

    `listing_id` today; `property_id` once entity resolution lands in Phase 4.
    The DB CHECK enforces that exactly one is set — see migration 0003 for why
    a degenerate properties row per listing was rejected.
    """

    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("listing_id", "weights_version", "score_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    property_id: Mapped[int | None] = mapped_column(BigInteger)
    weights_version: Mapped[int] = mapped_column(
        ForeignKey("score_weights.version")
    )
    overall: Mapped[float] = mapped_column(Numeric)     # 0-100
    # Share of non-zero weight that produced a value; < 1.0 when factors
    # abstained for lack of data on this listing.
    coverage: Mapped[float] = mapped_column(Numeric, default=1.0)
    score_date: Mapped[date] = mapped_column(Date)      # Asia/Kolkata day
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScoreFactor(Base):
    """One factor's contribution, with the rows it was computed from.

    'Evidence or it didn't happen' (plan §2): a factor that cannot cite its
    source rows is not allowed to move the score. Factors with no data are
    still written, with evidence {"kind": "no_data", ...}, so a gap is visible
    in the decomposition instead of silently absent.
    """

    __tablename__ = "score_factors"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("scores.id"))
    factor: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Numeric)       # 0-1, pre-weighting
    evidence: Mapped[dict] = mapped_column(JSONB)


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, unique=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content: Mapped[dict] = mapped_column(JSONB)
    source_health: Mapped[list | dict] = mapped_column(JSONB)
    # Null until the digest is actually delivered. UNIQUE(report_date) stops a
    # duplicate row, not a duplicate email — this stops the email.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_run_id: Mapped[int | None] = mapped_column(ForeignKey("report_runs.id"))
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    property_id: Mapped[int | None] = mapped_column(BigInteger)
    tier: Mapped[str] = mapped_column(Text)             # instant | daily
    headline: Mapped[str] = mapped_column(Text)
    score_id: Mapped[int | None] = mapped_column(ForeignKey("scores.id"))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    feedback: Mapped[int | None] = mapped_column(SmallInteger)  # +1 / -1 / null
