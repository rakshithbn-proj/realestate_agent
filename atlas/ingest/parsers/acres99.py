"""Parser for `fatihtahta/99acres-scraper-ppe` actor output — the plot source.

This is the source that makes the land/JD thesis testable: MagicBricks returns
no land at all, so until this lands the corridors were chosen for an asset
class Atlas could not see (handoff §3 finding 1).

The feed is rich and mostly well-behaved, but it carries three traps. Each is
handled below with a cross-check rather than trust, because all three fail
*silently* — they produce plausible numbers, not errors:

1. **`property.area.min_area_sqft` is square metres despite the name.** A
   1,200 sqft plot reports `111.4836`. Ingested naively every Rs/sqft figure
   is wrong by 10.76x, which would make land look like a 90% discount against
   apartments and put fabricated bargains at the top of the briefing.
   Confirmed in the saved fixture: the same record carries
   `super_sqft: 1200`, `display_area: "1200 sqft"` and
   `secondary_area: "111.48 sq.m."`.

2. **The feed mixes listings with builder projects.** A project is not a
   purchasable unit. Those return SKIP, not None — see parsers/__init__.py for
   why the distinction matters to run health.

3. **Coordinates are sometimes transposed** — a Sarjapur Road plot came back
   as `latitude: 77.618233, longitude: 13.002091`. Handled in
   `common.normalise_coords`; a plain range check does not catch it.

Bump PARSER_VERSION on any mapping change (plan.md §7).
"""
from typing import Any

from atlas.ingest.parsers.common import (
    canon_rera_ids,
    normalise_coords,
    norm_text,
    parse_price,
    parse_timestamp,
    to_float,
)

PARSER_VERSION = "acres99/1.0.0"

SQM_TO_SQFT = 10.7639

# How far a candidate area may sit from the price-derived one and still be
# trusted. The portal rounds `price_sqft` to whole rupees, which on the saved
# fixture puts the two up to ~1.8% apart; 5% leaves headroom for that without
# being wide enough to admit a unit error (the m2 confusion is 976% off).
_AREA_TOLERANCE = 0.05

# 99acres labels the seller on the contact card.
_LISTER_KIND = {"owner": "owner", "dealer": "broker", "broker": "broker",
                "builder": "builder", "agent": "broker",
                "featured dealer": "broker"}


def _get(raw: dict, *path: str) -> Any:
    """Nested lookup that tolerates missing branches."""
    node: Any = raw
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def is_listing(raw: dict) -> bool:
    """A purchasable unit, as opposed to a builder project card.

    Two independent tests, because either alone is fragile: `record_type`
    is the actor's own discriminator, and a per-unit `property_id` is what
    makes a record addressable at all. A project has neither.
    """
    record_type = (raw.get("record_type") or "").strip().lower()
    if record_type and record_type != "property_listing":
        return False
    property_id = (_get(raw, "listing", "property_id")
                   or _get(raw, "entity", "external_ids", "property_id"))
    return bool(property_id)


def _agrees(a: float, b: float) -> bool:
    return b > 0 and abs(a - b) / b <= _AREA_TOLERANCE


def _corroborating_areas(area: dict) -> dict[str, float]:
    """Every independent read on the area the record carries, in sqft.

    The record states the same figure four ways, and they are genuinely
    independent fields rather than one value copied around: `super_sqft`
    (number), `display_area` ("1200 sqft", a string), `min_area_sqft`
    (square metres despite the name), and `secondary_area` ("111.48 sq.m.",
    a string). Agreement across them is what makes an area trustworthy.
    """
    reads: dict[str, float] = {}

    display = to_float(area.get("display_area"))
    if display and display > 0 and "sqft" in str(area.get("display_area", "")).lower():
        reads["display_area"] = display

    metres = to_float(area.get("min_area_sqft"))
    if metres and metres > 0:
        reads["min_area_sqft_as_sqm"] = metres * SQM_TO_SQFT

    secondary = str(area.get("secondary_area") or "")
    if "sq.m" in secondary.lower():
        value = to_float(secondary)
        if value and value > 0:
            reads["secondary_area_sqm"] = value * SQM_TO_SQFT
    return reads


def resolve_area_sqft(raw: dict) -> tuple[float | None, str]:
    """Area in square feet, plus how it was derived.

    Never trusts a single field. The method string is carried into the parsed
    dict and archived in listing_versions.snapshot, so a future unit bug is
    attributable rather than a mystery.

    **The price cross-check is a unit guard, not a tie-breaker.** That
    distinction is load-bearing and the saved fixture is why: on four of its
    24 records `super_sqft`, `display_area` and `secondary_area` all agree at
    1,200 sqft while `price / price_sqft` implies 1,280 — the seller rounded
    the rate, not the plot. An earlier version preferred the arithmetic there
    and silently invented a 7% larger plot. Corroborated area wins; price
    arithmetic only decides when nothing corroborates, or when the gap is the
    size of a unit error (~10.76x) rather than a rounding.

    Returns (None, 'unresolved') when nothing corroborates. `price_per_sqft`
    is a generated column, so it simply goes null and no corrupt Rs/sqft ever
    reaches a score — a missing area beats a confident wrong one.
    """
    area = _get(raw, "property", "area") or {}
    pricing = raw.get("pricing") or {}

    # Rs 55,20,001 / Rs 4,600 per sqft = 1,200 sqft — the arithmetic that
    # caught the m2 bug originally.
    price = to_float(pricing.get("min_price")) or to_float(pricing.get("average_price"))
    ppsf = (to_float(pricing.get("price_sqft"))
            or to_float(pricing.get("price_per_unit_area")))
    implied = price / ppsf if price and ppsf and ppsf > 0 else None
    reads = _corroborating_areas(area)

    for key in ("super_sqft", "builtup_sqft", "carpet_sqft"):
        value = to_float(area.get(key))
        if not value or value <= 0:
            continue
        agreeing = [name for name, other in reads.items() if _agrees(value, other)]
        if agreeing:
            # A unit error would put the value ~10.76x from the price-implied
            # area. Anything smaller is the seller's rounding, and the
            # corroborated figure stands.
            if implied is not None and not _plausible_against(value, implied):
                return implied, (f"derived_from_price ({key}={value} is off by a "
                                 "unit-sized factor)")
            return value, f"{key} (agrees with {', '.join(sorted(agreeing))})"
        if implied is not None and _agrees(value, implied):
            return value, f"{key} (agrees with price/sqft)"
        return value, f"{key} (uncorroborated)"

    # No plain sqft field: fall back to the converted metres reading, then to
    # the price arithmetic.
    if "min_area_sqft_as_sqm" in reads:
        converted = reads["min_area_sqft_as_sqm"]
        if implied is None or _plausible_against(converted, implied):
            return converted, "min_area_sqft x 10.7639 (field is square metres)"
    if implied is not None:
        return implied, "derived_from_price (no usable area field)"
    return None, "unresolved"


def _plausible_against(value: float, implied: float) -> bool:
    """Is `value` within a rounding of `implied`, rather than a unit error?

    Accepts a wide band (half to double) deliberately: sellers fudge the
    quoted rate, so the two often differ by 5-25%. What must never pass is a
    square-metre value read as square feet, which is off by 10.76x.
    """
    if implied <= 0:
        return True
    ratio = value / implied
    return 0.5 <= ratio <= 2.0


def parse(raw: dict) -> dict | None | Any:
    """Raw actor item -> listings-shaped dict, None (failure), or SKIP."""
    from atlas.ingest.parsers import SKIP

    if not isinstance(raw, dict):
        return None
    if not is_listing(raw):
        return SKIP

    ext = (_get(raw, "listing", "property_id")
           or _get(raw, "entity", "external_ids", "property_id"))
    if not ext:
        return None

    area_sqft, area_method = resolve_area_sqft(raw)
    lat, lon = normalise_coords(_get(raw, "location", "coordinates", "latitude"),
                                _get(raw, "location", "coordinates", "longitude"))

    # locality_name is the clean value ("Sarjapur"); `locality` carries the
    # city too ("Sarjapur, Bangalore"). Corridor matching is substring-based
    # so either works, but the clean one keeps the localities table tidy.
    locality = norm_text(_get(raw, "location", "locality_name")
                         or _get(raw, "location", "locality"))

    project = norm_text(_get(raw, "relationships", "project", "project_name")
                        or _get(raw, "location", "society_name"))
    lister_raw = norm_text(_get(raw, "contact_details", "class_label"))
    price = (parse_price(_get(raw, "pricing", "min_price"))
             or parse_price(_get(raw, "pricing", "display_price")))

    return {
        "external_id": str(ext),
        "title": norm_text(_get(raw, "listing", "title")
                           or _get(raw, "entity", "title")),
        "project_raw": project,
        "project_norm": project.lower() if project else None,
        "address_raw": norm_text(_get(raw, "location", "address")),
        "locality": locality,
        # The registry market slug is authoritative downstream; the portal's
        # own value ("Bangalore East") stays in the snapshot as evidence.
        "city": norm_text(_get(raw, "location", "city")),
        "lat": lat,
        "lon": lon,
        "property_type": norm_text(_get(raw, "listing", "property_type")),
        "bhk": None,          # land has no bedrooms
        "floor": None,
        "area_sqft": area_sqft,
        "price_inr": price,
        "lister_kind": _LISTER_KIND.get((lister_raw or "").lower(), lister_raw),
        # The actor masks contact numbers; nothing to carry.
        "lister_phone": None,
        # Both the unit's own registration and the parent project's — either
        # can be the one that joins to the Karnataka registry.
        "rera_ids": canon_rera_ids(
            _get(raw, "attributes", "compliance", "rera_registration_id"),
            _get(raw, "attributes", "compliance", "project_rera_registration_id"),
        ),
        "description": norm_text(_get(raw, "listing", "description")
                                 or _get(raw, "entity", "description")),
        "url": norm_text(_get(raw, "listing", "details_url")
                         or _get(raw, "entity", "url")
                         or _get(raw, "source_context", "canonical_url")),
        "posted_at": parse_timestamp(_get(raw, "listing", "posted_at")
                                     or _get(raw, "availability", "listing_posted_at")),
        # Not a listings column — carried so it lands in the version snapshot,
        # which is where a future unit bug gets diagnosed from.
        "area_method": area_method,
        "ownership": norm_text(_get(raw, "property", "ownership_label")),
    }
