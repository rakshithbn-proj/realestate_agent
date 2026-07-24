"""Raw-first ingestion pipeline (plan.md §2: raw first, parse second).

For one source run:
  1. open a scrape_run row
  2. fetch raw items and store EVERY one in raw_payloads before parsing
  3. parse each item (versioned parser) and upsert into listings, recording
     change history in listing_versions and price moves in price_events
  4. close the run with a status health monitoring can act on

Re-running a day is safe: upserts are keyed on (source_id, external_id) and a
re-observation with no changes only bumps last_seen_at (plan.md §7 idempotent
jobs).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.ingest import geohash
from atlas.ingest.fetchers import FETCHERS
from atlas.ingest.parsers import PARSERS
from atlas.ingest.registry import SourceSpec
from atlas.models import (
    Listing,
    ListingVersion,
    Locality,
    PriceEvent,
    RawPayload,
    ScrapeRun,
    Source,
)

log = logging.getLogger(__name__)

# Fields whose change constitutes an 'updated' version (price handled separately).
TRACKED_FIELDS = (
    "title", "project_raw", "project_norm", "locality", "city", "lat", "lon",
    "property_type", "bhk", "floor", "area_sqft", "lister_kind", "lister_phone",
    "rera_ids", "description", "url", "status",
)


@dataclass
class RunResult:
    run_id: int
    status: str
    items_found: int
    parsed: int
    failed: int
    new: int
    updated: int
    price_changed: int
    unchanged: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_source(session: Session, spec: SourceSpec) -> Source:
    source = session.scalar(
        select(Source).where(Source.name == spec.name, Source.city == spec.city)
    )
    if source is None:
        source = Source(
            name=spec.name,
            city=spec.city,
            kind=spec.kind,
            fetcher=f"{spec.fetcher}:{spec.params.get('actor', spec.params.get('path', ''))}",
            expected_daily_volume=spec.expected_daily_volume,
        )
        session.add(source)
        session.flush()
    return source


def _get_or_create_locality(session: Session, city: str, name: str) -> Locality:
    locality = session.scalar(
        select(Locality).where(Locality.city == city, Locality.name == name)
    )
    if locality is None:
        locality = Locality(city=city, name=name)
        session.add(locality)
        session.flush()
    return locality


def _apply_parsed(listing: Listing, parsed: dict, parser_version: str) -> None:
    listing.title = parsed["title"]
    listing.project_raw = parsed["project_raw"]
    listing.project_norm = parsed["project_norm"]
    listing.address_raw = parsed["address_raw"]
    listing.city = parsed["city"]
    listing.lat = parsed["lat"]
    listing.lon = parsed["lon"]
    listing.geohash6 = (
        geohash.encode(parsed["lat"], parsed["lon"])
        if parsed["lat"] is not None and parsed["lon"] is not None
        else None
    )
    listing.property_type = parsed["property_type"]
    listing.bhk = parsed["bhk"]
    listing.floor = parsed["floor"]
    listing.area_sqft = parsed["area_sqft"]
    listing.price_inr = parsed["price_inr"]
    listing.lister_kind = parsed["lister_kind"]
    listing.lister_phone = parsed["lister_phone"]
    listing.rera_ids = parsed["rera_ids"]
    listing.description = parsed["description"]
    listing.url = parsed["url"]
    listing.parser_version = parser_version
    listing.last_seen_at = _now()


def _upsert_listing(
    session: Session,
    source: Source,
    spec: SourceSpec,
    parsed: dict,
    run: ScrapeRun,
) -> str:
    """Insert or update one listing; return the change kind observed
    ('new' | 'updated' | 'price_changed' | 'unchanged')."""
    city = parsed["city"] or spec.city
    parsed["city"] = city
    if parsed["locality"]:
        locality = _get_or_create_locality(session, city, parsed["locality"])
        locality_id = locality.id
    else:
        locality_id = None

    listing = session.scalar(
        select(Listing).where(
            Listing.source_id == source.id,
            Listing.external_id == parsed["external_id"],
        )
    )

    if listing is None:
        listing = Listing(
            source_id=source.id,
            external_id=parsed["external_id"],
            locality_id=locality_id,
            status="active",
            parser_version="",  # set by _apply_parsed
        )
        _apply_parsed(listing, parsed, PARSERS[spec.parser][1])
        session.add(listing)
        session.flush()
        session.add(
            ListingVersion(
                listing_id=listing.id,
                scrape_run_id=run.id,
                change_kind="new",
                snapshot=parsed,
            )
        )
        if parsed["price_inr"] is not None:
            session.add(
                PriceEvent(listing_id=listing.id, old_price=None,
                           new_price=parsed["price_inr"])
            )
        return "new"

    old_price = listing.price_inr
    new_price = parsed["price_inr"]
    price_changed = new_price is not None and old_price != new_price

    old_values = {
        "title": listing.title, "project_raw": listing.project_raw,
        "project_norm": listing.project_norm, "locality": None,
        "city": listing.city, "lat": listing.lat, "lon": listing.lon,
        "property_type": listing.property_type, "bhk": listing.bhk,
        "floor": listing.floor,
        "area_sqft": float(listing.area_sqft) if listing.area_sqft is not None else None,
        "lister_kind": listing.lister_kind, "lister_phone": listing.lister_phone,
        "rera_ids": list(listing.rera_ids or []),
        "description": listing.description, "url": listing.url,
        "status": listing.status,
    }
    other_changed = any(
        field != "locality" and old_values.get(field) != parsed.get(field, old_values.get(field))
        for field in TRACKED_FIELDS
    )

    listing.locality_id = locality_id
    _apply_parsed(listing, parsed, PARSERS[spec.parser][1])

    if price_changed:
        pct = (
            round((new_price - old_price) / old_price * 100, 2)
            if old_price
            else None
        )
        session.add(
            PriceEvent(listing_id=listing.id, old_price=old_price,
                       new_price=new_price, pct_change=pct)
        )
        session.add(
            ListingVersion(listing_id=listing.id, scrape_run_id=run.id,
                           change_kind="price_changed", snapshot=parsed)
        )
        return "price_changed"
    if other_changed:
        session.add(
            ListingVersion(listing_id=listing.id, scrape_run_id=run.id,
                           change_kind="updated", snapshot=parsed)
        )
        return "updated"
    return "unchanged"


def run_source(session: Session, spec: SourceSpec) -> RunResult:
    source = _get_or_create_source(session, spec)
    run = ScrapeRun(source_id=source.id, status="running")
    session.add(run)
    session.commit()

    try:
        raw_items = FETCHERS[spec.fetcher](spec.params)
    except Exception as exc:  # failure is recorded, never swallowed (plan §7)
        run.status = "failed"
        run.error = f"fetch: {exc}"
        run.finished_at = _now()
        run.items_found = 0
        session.commit()
        log.exception("fetch failed for %s/%s", spec.name, spec.city)
        return RunResult(run.id, "failed", 0, 0, 0, 0, 0, 0, 0)

    parse_fn = PARSERS[spec.parser][0]
    counts = {"new": 0, "updated": 0, "price_changed": 0, "unchanged": 0}
    failed = 0
    for raw in raw_items:
        parsed = None
        parse_error: str | None = None
        try:
            parsed = parse_fn(raw)
        except Exception as exc:
            parse_error = str(exc)

        # Raw payload is stored no matter what happened above.
        session.add(
            RawPayload(
                scrape_run_id=run.id,
                external_id=parsed["external_id"] if parsed else None,
                url=parsed["url"] if parsed else None,
                payload=raw,
            )
        )
        if parsed is None:
            failed += 1
            if parse_error:
                log.error("parse error in run %s: %s", run.id, parse_error)
            continue
        kind = _upsert_listing(session, source, spec, parsed, run)
        counts[kind] += 1

    run.items_found = len(raw_items)
    run.finished_at = _now()
    parsed_total = sum(counts.values())
    if parsed_total == 0 and raw_items:
        run.status = "failed"
        run.error = f"all {len(raw_items)} items failed to parse"
    elif failed:
        run.status = "anomalous"
        run.error = f"{failed}/{len(raw_items)} items failed to parse"
    else:
        run.status = "ok"
    session.commit()

    return RunResult(
        run_id=run.id, status=run.status, items_found=len(raw_items),
        parsed=parsed_total, failed=failed, new=counts["new"],
        updated=counts["updated"], price_changed=counts["price_changed"],
        unchanged=counts["unchanged"],
    )
