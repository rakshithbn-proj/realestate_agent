"""Raw-first ingestion pipeline (plan.md §2: raw first, parse second).

For one source run:
  1. open a scrape_run row
  2. fetch raw items, parse them (pure, per-item error containment), and
     commit EVERY raw item to raw_payloads before any listing is touched —
     an upsert crash can never lose raw data
  3. upsert each parsed item into listings inside a savepoint, recording
     change history in listing_versions and price moves in price_events;
     a failing item is logged with its raw_payload id and skipped
  4. close the run with a status health monitoring can act on — the run row
     always reaches a terminal state, even on unexpected errors

Re-running a day is safe: upserts are keyed on (source_id, external_id) and a
re-observation with no changes only bumps last_seen_at (plan.md §7 idempotent
jobs).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.ingest import geohash
from atlas.ingest.fetchers import FETCHERS
from atlas.ingest.parsers import PARSERS, SKIP
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

# Listing columns compared against the parsed dict to detect an 'updated'
# observation. Price and locality_id are tracked separately.
TRACKED_FIELDS = (
    "title", "project_raw", "project_norm", "address_raw", "lat", "lon",
    "property_type", "bhk", "floor", "area_sqft", "lister_kind",
    "lister_phone", "rera_ids", "description", "url",
)


# Trial-validated anomaly thresholds (trial/config.py, handoff §7)
ANOMALY_VOLUME_RATIO = 0.5    # run smaller than half the trailing avg
ANOMALY_UNPARSED_RATIO = 0.3  # actor "SUCCEEDED" but items are stubs


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
    relisted: int
    unchanged: int
    # Valid records that are deliberately not listings (99acres mixes builder
    # projects into the feed). Counted apart from `failed` so a project-heavy
    # feed can never look like a dying scraper.
    skipped: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_posted_at(value) -> datetime | None:
    """Parsers emit posted_at as an ISO string (the parsed dict is archived as
    JSON); the column is timestamptz."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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
            # Mirrors the spec so the Phase-1 gate, which reads sources.enabled,
            # never demands a daily `ok` run from a source that is switched off.
            enabled=spec.enabled,
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


def _apply_parsed(listing: Listing, parsed: dict, market_city: str,
                  parser_version: str) -> None:
    listing.title = parsed["title"]
    listing.project_raw = parsed["project_raw"]
    listing.project_norm = parsed["project_norm"]
    listing.address_raw = parsed["address_raw"]
    # The registry market slug is authoritative; the portal-reported city
    # (parsed["city"]) stays in the snapshot as evidence. A portal spelling
    # change ('Bengaluru') must not fork the market into a new slug.
    listing.city = market_city
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
    # A vanished price (portal switched to "price on request", or a parse
    # regression) keeps the last known value — the disappearance is recorded
    # as an 'updated' version whose snapshot shows price_inr null, never as a
    # silent overwrite.
    if parsed["price_inr"] is not None:
        listing.price_inr = parsed["price_inr"]
    listing.lister_kind = parsed["lister_kind"]
    listing.lister_phone = parsed["lister_phone"]
    listing.rera_ids = parsed["rera_ids"]
    listing.description = parsed["description"]
    listing.url = parsed["url"]
    # The portal's posting date only ever moves backwards in confidence: once
    # known it is a fact about the listing, so a later payload that omits it
    # (a thinner search-result record, a parser regression) must not erase it
    # and silently reset days-on-market to zero.
    posted_at = _parse_posted_at(parsed.get("posted_at"))
    if posted_at is not None:
        listing.posted_at = posted_at
    listing.parser_version = parser_version
    listing.last_seen_at = _now()


def _stored_value(listing: Listing, field: str):
    """Listing column value normalised for comparison with the parsed dict."""
    value = getattr(listing, field)
    if field == "area_sqft" and value is not None:
        return float(value)
    if field == "rera_ids":
        return list(value or [])
    return value


def _upsert_listing(
    session: Session,
    source: Source,
    spec: SourceSpec,
    parsed: dict,
    run: ScrapeRun,
    parser_version: str,
) -> str:
    """Insert or update one listing; return the change kind observed
    ('new' | 'updated' | 'price_changed' | 'unchanged')."""
    if parsed["locality"]:
        locality_id = _get_or_create_locality(session, spec.city,
                                              parsed["locality"]).id
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
            parser_version=parser_version,
        )
        _apply_parsed(listing, parsed, spec.city, parser_version)
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
    price_vanished = new_price is None and old_price is not None
    was_removed = listing.status == "removed"

    other_changed = (
        locality_id != listing.locality_id
        or price_vanished
        or any(_stored_value(listing, f) != parsed[f] for f in TRACKED_FIELDS)
    )

    listing.locality_id = locality_id
    _apply_parsed(listing, parsed, spec.city, parser_version)

    def _record_price_move() -> None:
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

    if was_removed:
        # Same-portal reappearance under the same id. (Cross-listing relists
        # with NEW ids route through entity resolution in Phase 4 — plan §7.)
        listing.status = "relisted"
        listing.removed_at = None
        session.add(
            ListingVersion(listing_id=listing.id, scrape_run_id=run.id,
                           change_kind="relisted", snapshot=parsed)
        )
        # A price move across the relist is also recorded as a price_changed
        # version + PriceEvent, so consumers reading either signal see it.
        if price_changed:
            _record_price_move()
        return "relisted"

    if price_changed:
        _record_price_move()
        return "price_changed"
    if other_changed:
        session.add(
            ListingVersion(listing_id=listing.id, scrape_run_id=run.id,
                           change_kind="updated", snapshot=parsed)
        )
        return "updated"
    return "unchanged"


def run_source(session: Session, spec: SourceSpec) -> RunResult:
    # Resolve config before opening a run — an unknown fetcher/parser key is a
    # programming error and must not strand a 'running' row.
    fetch_fn = FETCHERS[spec.fetcher]
    parse_fn, parser_version = PARSERS[spec.parser]

    source = _get_or_create_source(session, spec)
    run = ScrapeRun(source_id=source.id, status="running")
    session.add(run)
    session.commit()

    try:
        raw_items = fetch_fn(spec.params)
    except Exception as exc:  # failure is recorded, never swallowed (plan §7)
        run.status = "failed"
        run.error = f"fetch: {exc}"
        run.finished_at = _now()
        run.items_found = 0
        session.commit()
        log.exception("fetch failed for %s/%s", spec.name, spec.city)
        return RunResult(run.id, "failed", 0, 0, 0, 0, 0, 0, 0, 0)

    counts = {"new": 0, "updated": 0, "price_changed": 0, "relisted": 0,
              "unchanged": 0}
    failed = 0
    skipped = 0
    errors: list[str] = []
    try:
        # Parse first (pure python, per-item containment) so the raw archive
        # can record each item's external id.
        parsed_items: list[tuple[dict, dict | None]] = []
        for raw in raw_items:
            try:
                parsed = parse_fn(raw)
            except Exception as exc:
                parsed = None
                errors.append(f"parse: {exc}")
                log.exception("parse error in run %s", run.id)
            parsed_items.append((raw, parsed))

        # Raw first: the entire archive is committed before any upsert, so a
        # listing-write failure can never roll back raw data.
        payload_rows = []
        for raw, parsed in parsed_items:
            usable = isinstance(parsed, dict)
            row = RawPayload(
                scrape_run_id=run.id,
                external_id=parsed["external_id"] if usable else None,
                url=parsed["url"] if usable else None,
                payload=raw,
            )
            session.add(row)
            payload_rows.append(row)
        session.commit()

        for (raw, parsed), payload_row in zip(parsed_items, payload_rows):
            if parsed is SKIP:
                # A valid record that isn't a listing (a builder project).
                # Archived above like everything else, so if that judgement is
                # ever wrong it is re-derivable from raw_payloads.
                skipped += 1
                continue
            if parsed is None:
                failed += 1
                continue
            try:
                with session.begin_nested():
                    kind = _upsert_listing(session, source, spec, parsed,
                                           run, parser_version)
                counts[kind] += 1
            except Exception as exc:
                # Savepoint rolled back this item only; raw payload is safe.
                failed += 1
                errors.append(
                    f"upsert raw_payload={payload_row.id} "
                    f"external_id={parsed['external_id']}: {exc}"
                )
                log.exception("upsert error in run %s (raw_payload %s)",
                              run.id, payload_row.id)
        session.commit()
    except Exception as exc:
        # Catastrophic (e.g. connection loss): the run row must still reach a
        # terminal state so health monitoring sees it.
        session.rollback()
        run.status = "failed"
        run.error = f"run aborted: {exc}"
        run.items_found = len(raw_items)
        run.finished_at = _now()
        session.commit()
        log.exception("run %s aborted for %s/%s", run.id, spec.name, spec.city)
        return RunResult(run.id, "failed", len(raw_items),
                         sum(counts.values()), failed, counts["new"],
                         counts["updated"], counts["price_changed"],
                         counts["relisted"], counts["unchanged"], skipped)

    run.items_found = len(raw_items)
    run.finished_at = _now()
    parsed_total = sum(counts.values())
    error_summary = "; ".join(errors)[:2000] or None
    volume_avg = _trailing_avg_items(session, source.id, exclude_run_id=run.id)
    # Skips are excluded from the denominator: they are records the parser
    # understood and chose not to store. Counting them as unparsed would let a
    # project-heavy feed cross ANOMALY_UNPARSED_RATIO, mark healthy runs
    # 'anomalous', and freeze the staleness sweep for that source.
    considered = len(raw_items) - skipped
    unparsed_ratio = failed / considered if considered else 0.0

    # Status classification. A run stays 'ok' — and so keeps authorizing the
    # staleness sweep — through a low rate of item failures; only a real
    # collapse turns it 'anomalous'. Otherwise a scraper that drops one stub a
    # day would silently freeze removal / days-on-market tracking forever
    # (the sweep guard needs a recent 'ok' run), with nothing surfaced.
    if not raw_items:
        # A portal returning zero items is a block/soft-failure, not an empty
        # market — never 'ok' (which would also poison the trailing average).
        run.status = "anomalous"
        run.error = "empty fetch: 0 items returned"
    elif parsed_total == 0 and considered == 0:
        # Every record was a builder project. Nothing broke, but a land search
        # that returns no purchasable unit is not a normal day either — flag
        # it without calling the parser dead.
        run.status = "anomalous"
        run.error = (f"no listings in {len(raw_items)} items: all were "
                     "non-listing records (builder projects)")
    elif parsed_total == 0:
        run.status = "failed"
        run.error = f"all {considered} listing items failed: {error_summary}"
    elif unparsed_ratio > ANOMALY_UNPARSED_RATIO:
        # Actor reported success but pushed mostly stubs — the acres99 failure
        # mode (handoff §7): silent data-quality collapse behind a green status.
        run.status = "anomalous"
        run.error = (f"unparsed ratio {failed}/{considered} exceeds "
                     f"{ANOMALY_UNPARSED_RATIO}: {error_summary}")
    elif volume_avg is not None and len(raw_items) < volume_avg * ANOMALY_VOLUME_RATIO:
        run.status = "anomalous"
        run.error = (f"volume collapse: {len(raw_items)} items vs trailing "
                     f"avg {volume_avg:.0f}")
    else:
        # Healthy run. Note any tolerated item failures without downgrading —
        # visible in the run row, but the source stays authorized to sweep.
        run.status = "ok"
        run.error = (f"{failed}/{considered} items failed (within tolerance): "
                     f"{error_summary}") if failed else None
    session.commit()

    return RunResult(
        run_id=run.id, status=run.status, items_found=len(raw_items),
        parsed=parsed_total, failed=failed, new=counts["new"],
        updated=counts["updated"], price_changed=counts["price_changed"],
        relisted=counts["relisted"], unchanged=counts["unchanged"],
        skipped=skipped,
    )


def _trailing_avg_items(session: Session, source_id: int, n: int = 7,
                        exclude_run_id: int | None = None) -> float | None:
    query = (
        select(ScrapeRun.items_found)
        .where(ScrapeRun.source_id == source_id,
               ScrapeRun.status == "ok",
               ScrapeRun.items_found.isnot(None))
        .order_by(ScrapeRun.started_at.desc())
        .limit(n)
    )
    if exclude_run_id is not None:
        query = query.where(ScrapeRun.id != exclude_run_id)
    rows = session.scalars(query).all()
    return sum(rows) / len(rows) if rows else None


def sweep_stale_listings(session: Session, spec: SourceSpec,
                         stale_days: int = 7) -> int:
    """Mark active listings unseen for `stale_days` as removed.

    The per-run actor sample (~300 items) is NOT a full market snapshot, so
    absence from one run means nothing — removal is inferred from sustained
    absence. Guard (trial-validated): only sweep when the source has had an
    'ok' run more recent than the cutoff. A dead or anomalous scraper must
    never convert its own failure into thousands of fake removals — that
    would poison days-on-market, a core distress signal.
    """
    source = _get_or_create_source(session, spec)
    cutoff = _now() - timedelta(days=stale_days)

    healthy_run_since_cutoff = session.scalar(
        select(ScrapeRun.id)
        .where(ScrapeRun.source_id == source.id,
               ScrapeRun.status == "ok",
               ScrapeRun.finished_at > cutoff)
        .limit(1)
    )
    if healthy_run_since_cutoff is None:
        log.warning("sweep skipped for %s/%s: no healthy run since %s",
                    spec.name, spec.city, cutoff.isoformat())
        return 0

    stale = session.scalars(
        select(Listing).where(
            Listing.source_id == source.id,
            Listing.status.in_(("active", "relisted")),
            Listing.last_seen_at < cutoff,
        )
    ).all()
    now = _now()
    for listing in stale:
        listing.status = "removed"
        listing.removed_at = now
        session.add(
            ListingVersion(
                listing_id=listing.id,
                scrape_run_id=None,
                change_kind="removed",
                snapshot={"reason": "stale",
                          "last_seen_at": listing.last_seen_at.isoformat(),
                          "stale_days": stale_days},
            )
        )
    session.commit()
    if stale:
        log.info("sweep: %d listings marked removed for %s/%s",
                 len(stale), spec.name, spec.city)
    return len(stale)
