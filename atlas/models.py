"""ORM models for the ingestion spine only.

DDL is owned by Alembic migrations (see alembic/versions/), which were derived
from docs/schema.sql. Tables that no code touches yet (scoring, contacts,
outcomes, ...) intentionally have no ORM class — they get one when their module
lands. These classes must stay in sync with the migrations for the tables they
map.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
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
