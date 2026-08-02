"""Helpers shared by every portal parser.

Extracted from the MagicBricks parser when the 99acres parser landed. They are
here rather than duplicated because each one encodes a decision that must hold
identically across sources — most importantly `canon_rera_ids`, which is what
lifts the registry join to 99.6% (handoff §7). Two copies would be two places
for that to drift.
"""
import re
from datetime import datetime, timezone
from typing import Any

# Number must start with a digit ("Rs. 95 Lac" must match "95", not the dot in
# "Rs."), and units are longest-first so "Lakh" never half-matches.
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(crore|cr|lakh|lac|l|k)?", re.IGNORECASE)
_MULT = {"cr": 1e7, "crore": 1e7, "l": 1e5, "lac": 1e5, "lakh": 1e5, "k": 1e3}

# Registry keys look like PRM/KA/RERA/...; portals prefix junk (e.g. TOR/).
# Canonicalising on the PRM/KA/RERA substring lifted the RERA join to 99.6%
# (handoff.md §7).
_RERA_RE = re.compile(r"PRM/KA/RERA/[A-Z0-9/]+", re.IGNORECASE)

# India's bounding box, used to catch transposed coordinates. Deliberately
# generous — the job is to reject nonsense, not to geofence Karnataka.
_LAT_RANGE = (6.0, 38.0)
_LON_RANGE = (68.0, 98.0)


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


def canon_rera_ids(*values: Any) -> list[str]:
    """Extract canonical PRM/KA/RERA/... ids from any number of fields.

    Variadic because 99acres carries the unit's own registration and the
    parent project's in separate fields, and either can be the one that joins.
    """
    found: set[str] = set()
    for value in values:
        if value:
            found.update(m.upper() for m in _RERA_RE.findall(str(value)))
    return sorted(found)


def norm_text(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # Digit-anchored with at most one decimal point, so junk like "12.9.3"
    # yields 12.9 instead of an uncaught ValueError killing the whole item.
    m = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group(0)) if m else None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def parse_timestamp(value: Any) -> str | None:
    """ISO-8601 string for listings.posted_at, or None.

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


def normalise_coords(lat: Any, lon: Any) -> tuple[float | None, float | None]:
    """Sanity-check a coordinate pair, transposing it if that is what fixes it.

    99acres really does emit swapped pairs — a Sarjapur Road plot came back as
    `latitude: 77.618233, longitude: 13.002091`. A plain range check misses it
    because 77.6 is a legal latitude *somewhere*; it is only wrong for India.
    Ingested as-is it produces a geohash pointing into the Barents Sea, which
    would silently poison the Phase-4 infrastructure-proximity model long after
    anyone remembers why.

    Returns (None, None) rather than a guess when neither orientation is
    plausible — no location beats a confident wrong one.
    """
    lat_f, lon_f = to_float(lat), to_float(lon)
    if lat_f is None or lon_f is None:
        return None, None

    def ok(la: float, lo: float) -> bool:
        return (_LAT_RANGE[0] <= la <= _LAT_RANGE[1]
                and _LON_RANGE[0] <= lo <= _LON_RANGE[1])

    if ok(lat_f, lon_f):
        return lat_f, lon_f
    if ok(lon_f, lat_f):
        return lon_f, lat_f
    return None, None
