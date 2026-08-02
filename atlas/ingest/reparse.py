"""Replay the raw archive through the current parser.

This is the payoff for the raw-first rule (plan.md §2): "parser bugs must be
recoverable by re-parsing raw_payloads" has been an architectural claim since
Phase 0 and is only true once something actually does it. Its first real use
is backfilling `listings.posted_at`, which every MagicBricks payload has
carried since the trial and which the parser discarded until v1.1.0 — so real
days-on-market is recoverable for the entire archive rather than starting from
the day the column landed.

Deliberately narrow: it re-derives listing FIELDS from stored payloads. It
does not create listings, write listing_versions, emit price_events, or move
first_seen_at / last_seen_at / status. A re-parse is a correction to what we
extracted, not a new observation — treating it as one would fabricate change
history and corrupt days-on-market, the exact signal this exists to fix.
"""
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.ingest import geohash
from atlas.ingest.parsers import PARSERS
from atlas.ingest.pipeline import _parse_posted_at
from atlas.ingest.registry import SOURCES, SourceSpec
from atlas.models import Listing, Locality, RawPayload, ScrapeRun, Source

log = logging.getLogger(__name__)

# Fields a re-parse may correct. Excludes price_inr, status, and the
# *_seen_at timestamps: those are observations, not extractions, and rewriting
# them from an old payload would rewrite history.
REPARSED_FIELDS = (
    "title", "project_raw", "project_norm", "address_raw", "lat", "lon",
    "property_type", "bhk", "floor", "area_sqft", "lister_kind",
    "lister_phone", "rera_ids", "description", "url",
)


@dataclass
class ReparseResult:
    source: str
    payloads: int
    parsed: int
    listings_updated: int
    posted_at_filled: int
    unmatched: int      # payloads whose listing no longer exists


def reparse_source(session: Session, spec: SourceSpec,
                   dry_run: bool = False) -> ReparseResult:
    parse_fn, parser_version = PARSERS[spec.parser]
    source = session.scalar(
        select(Source).where(Source.name == spec.name, Source.city == spec.city)
    )
    if source is None:
        return ReparseResult(f"{spec.name}/{spec.city}", 0, 0, 0, 0, 0)

    listings = {
        listing.external_id: listing
        for listing in session.scalars(
            select(Listing).where(Listing.source_id == source.id)
        )
    }
    localities = {
        (loc.city, loc.name): loc.id
        for loc in session.scalars(select(Locality))
    }

    payloads = session.scalars(
        select(RawPayload)
        .join(ScrapeRun, RawPayload.scrape_run_id == ScrapeRun.id)
        .where(ScrapeRun.source_id == source.id)
        # Oldest first, so the newest payload wins — replaying the archive in
        # the order it arrived leaves exactly the state a fresh run would.
        .order_by(RawPayload.fetched_at)
    )

    counted = parsed_ok = updated = filled = unmatched = 0
    touched: set[int] = set()
    for payload in payloads:
        counted += 1
        if payload.payload is None:
            continue
        try:
            parsed = parse_fn(payload.payload)
        except Exception:
            log.exception("reparse: parser raised on raw_payload %s", payload.id)
            continue
        if not isinstance(parsed, dict):
            continue          # None (unusable) or a SKIP sentinel
        parsed_ok += 1

        listing = listings.get(parsed["external_id"])
        if listing is None:
            unmatched += 1
            continue

        changed = False
        posted_at = _parse_posted_at(parsed.get("posted_at"))
        if posted_at is not None and listing.posted_at != posted_at:
            if listing.posted_at is None:
                filled += 1
            listing.posted_at = posted_at
            changed = True

        for field in REPARSED_FIELDS:
            if field not in parsed:
                continue
            current = getattr(listing, field)
            new = parsed[field]
            if field == "area_sqft" and current is not None:
                current = float(current)
            if field == "rera_ids":
                current = list(current or [])
                new = list(new or [])
            if current != new:
                setattr(listing, field, new)
                changed = True

        if parsed["locality"]:
            locality_id = localities.get((spec.city, parsed["locality"]))
            if locality_id is not None and listing.locality_id != locality_id:
                listing.locality_id = locality_id
                changed = True

        if changed:
            listing.geohash6 = (
                geohash.encode(listing.lat, listing.lon)
                if listing.lat is not None and listing.lon is not None
                else None
            )
            listing.parser_version = parser_version
            touched.add(listing.id)

    updated = len(touched)
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return ReparseResult(f"{spec.name}/{spec.city}", counted, parsed_ok,
                         updated, filled, unmatched)


def reparse(session: Session, source: str | None = None,
            dry_run: bool = False) -> list[ReparseResult]:
    """Re-parse one registry source, or all of them."""
    if source is not None:
        if source not in SOURCES:
            raise KeyError(f"unknown source {source!r}; known: {list(SOURCES)}")
        specs = [SOURCES[source]]
    else:
        specs = list(SOURCES.values())
    return [reparse_source(session, spec, dry_run=dry_run) for spec in specs]
