"""Parser for `thirdwatch/magicbricks-scraper` actor output.

Ported from the trial's normalize.py (field mapping verified against a real
run) and reshaped to target the `listings` table. Bump PARSER_VERSION on any
change to the mapping — it is stamped on every parsed row (plan.md §7).
"""
import re
from datetime import datetime, timezone
from typing import Any

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

# Number must start with a digit ("Rs. 95 Lac" must match "95", not the dot in
# "Rs."), and units are longest-first so "Lakh" never half-matches.
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(crore|cr|lakh|lac|l|k)?", re.IGNORECASE)
_MULT = {"cr": 1e7, "crore": 1e7, "l": 1e5, "lac": 1e5, "lakh": 1e5, "k": 1e3}

# Registry keys look like PRM/KA/RERA/...; portals prefix junk (e.g. TOR/).
# Canonicalising on the PRM/KA/RERA substring lifted the RERA join to 99.6%
# (handoff.md §7).
_RERA_RE = re.compile(r"PRM/KA/RERA/[A-Z0-9/]+", re.IGNORECASE)

_LISTER_KIND = {"agent": "broker", "broker": "broker", "owner": "owner",
                "builder": "builder", "developer": "builder"}


def parse_price(value: Any) -> int | None:
    """'2.42 Cr' -> 24200000; '95 Lac' -> 9500000; 24202000 -> 24202000."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        iv = int(value)
        return iv if iv > 0 else None
    m = _PRICE_RE.search(str(value).replace(",", ""))
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return int(num * _MULT.get(unit, 1)) or None


def canon_rera_ids(value: Any) -> list[str]:
    """Extract canonical PRM/KA/RERA/... ids (a listing may carry several)."""
    if not value:
        return []
    found = _RERA_RE.findall(str(value))
    return sorted({m.upper() for m in found})


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def parse_timestamp(value: Any) -> str | None:
    """ISO-8601 string for the listings.posted_at column, or None.

    Returned as a string rather than a datetime so the parsed dict stays
    JSON-serialisable — it is archived verbatim in listing_versions.snapshot.
    An unparseable date yields None: a wrong posting date is worse than a
    missing one, because days-on-market is a distress signal and a bogus
    'listed 400 days ago' would manufacture urgency that isn't there.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds vs milliseconds: anything past ~2001 in seconds is
        # beyond 1e9, so a value above 1e11 can only be milliseconds.
        seconds = float(value) / 1000 if float(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    # Digit-anchored with at most one decimal point, so junk like "12.9.3"
    # yields 12.9 instead of an uncaught ValueError killing the whole item.
    m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


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
        "lat": _to_float(_pick(raw, "lat")),
        "lon": _to_float(_pick(raw, "lon")),
        "property_type": norm_text(_pick(raw, "property_type")),
        "bhk": _to_int(_pick(raw, "bhk")),
        "floor": _to_int(_pick(raw, "floor")),
        "area_sqft": _to_float(_pick(raw, "area_sqft")),
        "price_inr": price,
        "lister_kind": _LISTER_KIND.get((lister_raw or "").lower(), lister_raw),
        "lister_phone": norm_text(_pick(raw, "lister_phone")),
        "rera_ids": canon_rera_ids(_pick(raw, "rera_id")),
        "description": norm_text(_pick(raw, "description")),
        "url": norm_text(_pick(raw, "url")),
        "posted_at": parse_timestamp(_pick(raw, "posted_at")),
    }
