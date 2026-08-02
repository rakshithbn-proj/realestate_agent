"""Parser for `thirdwatch/magicbricks-scraper` actor output.

Ported from the trial's normalize.py (field mapping verified against a real
run) and reshaped to target the `listings` table. Bump PARSER_VERSION on any
change to the mapping — it is stamped on every parsed row (plan.md §7).
"""
from typing import Any

from atlas.ingest.parsers.common import (
    canon_rera_ids,
    norm_text,
    parse_price,
    parse_timestamp,
    to_float,
    to_int,
)

PARSER_VERSION = "magicbricks/1.1.0"

# Per canonical field: ordered candidate keys in the raw actor item.
CANDIDATES: dict[str, list[str]] = {
    "external_id": ["listing_id", "listingId", "id", "propertyId", "property_id"],
    "title": ["title", "propertyTitle", "name", "heading"],
    "price_inr": ["price_inr", "price", "expectedPrice", "priceInr"],
    "price_display": ["price_display", "priceDisplay", "formattedPrice"],
    "area_sqft": ["super_area_sqft", "carpet_area_sqft", "builtup_area_sqft",
                  "area_sqft", "area", "superArea", "carpetArea"],
    "bhk": ["bhk", "bedrooms", "beds"],
    "floor": ["floor", "floorNumber"],
    "property_type": ["propertyType", "property_type", "type"],
    "locality": ["locality", "localityName", "location"],
    "city": ["city", "cityName"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "lng"],
    "project": ["project_name", "projectName", "society", "project"],
    "rera_id": ["rera_id", "reraId", "rera"],
    "lister_kind": ["listed_by", "listedBy", "seller_type", "posted_by"],
    "lister_phone": ["contact_phone", "contactPhone", "phone", "mobile"],
    "description": ["description", "propertyDescription", "desc"],
    "url": ["url", "detail_url", "detailUrl", "link", "property_url"],
    # The portal's own posting date. Present in the actor payload since the
    # trial and discarded until v1.1.0 — without it, days-on-market can only
    # mean "days since Atlas noticed", which reads every listing as brand new
    # until Atlas has been collecting for months. Backfillable by re-parsing
    # raw_payloads (`atlas.cli reparse`).
    "posted_at": ["posted_at", "postedAt", "postedOn", "listedOn",
                  "created_at", "listing_posted_at"],
}

_LISTER_KIND = {"agent": "broker", "broker": "broker", "owner": "owner",
                "builder": "builder", "developer": "builder"}


def _pick(raw: dict, field: str) -> Any:
    for key in CANDIDATES[field]:
        if key in raw and raw[key] not in (None, "", [], {}):
            return raw[key]
    return None


def parse(raw: dict) -> dict | None:
    """Raw actor item -> listings-shaped dict. None when there is no usable id
    (the caller counts that as a parse failure; the raw payload is stored
    regardless, so nothing is lost)."""
    ext = _pick(raw, "external_id")
    if ext is None:
        return None

    price = parse_price(_pick(raw, "price_inr"))
    if price is None:
        price = parse_price(_pick(raw, "price_display"))

    project = norm_text(_pick(raw, "project"))
    lister_raw = norm_text(_pick(raw, "lister_kind"))
    city = norm_text(_pick(raw, "city"))

    return {
        "external_id": str(ext),
        "title": norm_text(_pick(raw, "title")),
        "project_raw": project,
        "project_norm": project.lower() if project else None,
        "address_raw": None,
        "locality": norm_text(_pick(raw, "locality")),
        "city": city.lower() if city else None,
        "lat": to_float(_pick(raw, "lat")),
        "lon": to_float(_pick(raw, "lon")),
        "property_type": norm_text(_pick(raw, "property_type")),
        "bhk": to_int(_pick(raw, "bhk")),
        "floor": to_int(_pick(raw, "floor")),
        "area_sqft": to_float(_pick(raw, "area_sqft")),
        "price_inr": price,
        "lister_kind": _LISTER_KIND.get((lister_raw or "").lower(), lister_raw),
        "lister_phone": norm_text(_pick(raw, "lister_phone")),
        "rera_ids": canon_rera_ids(_pick(raw, "rera_id")),
        "description": norm_text(_pick(raw, "description")),
        "url": norm_text(_pick(raw, "url")),
        "posted_at": parse_timestamp(_pick(raw, "posted_at")),
    }
