"""Map raw actor output to the trial's common listing shape.

MagicBricks mapping is verified against a real run. For the other sources the
candidate lists cover likely field names; anything unmapped still lands in
raw_items, and the report's field-coverage table flags gaps immediately.
"""
import re
from typing import Any

# Per canonical field: ordered candidate keys in the raw item.
CANDIDATES: dict[str, list[str]] = {
    "external_id": ["listing_id", "listingId", "id", "propertyId", "property_id", "prop_id"],
    "title": ["title", "propertyTitle", "name", "heading"],
    "price_inr": ["price_inr", "price", "expectedPrice", "priceInr", "sale_price"],
    "price_display": ["price_display", "priceDisplay", "formattedPrice", "price_text"],
    "price_per_sqft": ["price_per_sqft", "pricePerSqft", "rate_per_sqft"],
    "area_sqft": ["super_area_sqft", "carpet_area_sqft", "builtup_area_sqft", "area_sqft",
                  "area", "superArea", "carpetArea", "size_sqft"],
    "bhk": ["bhk", "bedrooms", "bedroom_count", "beds"],
    "property_type": ["propertyType", "property_type", "type"],
    "locality": ["locality", "localityName", "neighborhood", "area_name", "location"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "lng"],
    "project": ["project_name", "projectName", "society", "building_name", "project"],
    "developer": ["developer", "builder", "builderName"],
    "rera_id": ["rera_id", "reraId", "rera"],
    "lister_kind": ["listed_by", "listedBy", "seller_type", "sellerType", "posted_by", "ownerType"],
    "contact_name": ["contact_name", "contactName", "owner_name", "ownerName", "agent_name"],
    "url": ["url", "detail_url", "detailUrl", "link", "property_url"],
    "posted_at": ["posted_at", "postedAt", "posted_date", "postedDate", "created_at"],
}

_PRICE_RE = re.compile(r"([\d.,]+)\s*(cr|crore|l|lac|lakh|k)?", re.IGNORECASE)
_MULT = {"cr": 1e7, "crore": 1e7, "l": 1e5, "lac": 1e5, "lakh": 1e5, "k": 1e3}


def parse_price(value: Any) -> int | None:
    """'2.42 Cr' -> 24200000; '95 Lac' -> 9500000; 24202000 -> 24202000."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    m = _PRICE_RE.search(str(value).replace(",", ""))
    if not m or not m.group(1):
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    return int(num * _MULT.get(unit, 1)) or None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d.]+", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _pick(raw: dict, field: str) -> Any:
    for key in CANDIDATES[field]:
        if key in raw and raw[key] not in (None, "", [], {}):
            return raw[key]
    return None


def normalize(raw: dict, source: str) -> dict:
    """Return the common listing dict. external_id may be None (counted as a
    normalization failure by the caller — the item still lands in raw_items)."""
    price = parse_price(_pick(raw, "price_inr"))
    if price is None:
        price = parse_price(_pick(raw, "price_display"))

    ext = _pick(raw, "external_id")
    return {
        "source": source,
        "external_id": str(ext) if ext is not None else None,
        "title": _pick(raw, "title"),
        "project": _pick(raw, "project"),
        "developer": _pick(raw, "developer"),
        "locality": _pick(raw, "locality"),
        "property_type": _pick(raw, "property_type"),
        "bhk": _to_int(_pick(raw, "bhk")),
        "area_sqft": _to_float(_pick(raw, "area_sqft")),
        "price_inr": price,
        "price_per_sqft": _to_float(_pick(raw, "price_per_sqft")),
        "lat": _to_float(_pick(raw, "lat")),
        "lon": _to_float(_pick(raw, "lon")),
        "rera_id": _pick(raw, "rera_id"),
        "lister_kind": _pick(raw, "lister_kind"),
        "contact_name": _pick(raw, "contact_name"),
        "url": _pick(raw, "url"),
        "posted_at": _pick(raw, "posted_at"),
    }
