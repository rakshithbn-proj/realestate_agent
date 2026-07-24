"""Minimal geohash encoder — enough for the 6-char blocking key on listings.

No external dependency; verified against known test vectors in the test suite.
"""

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 6) -> str:
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    result: list[str] = []
    bit = 0
    ch = 0
    even = True  # even bits take longitude
    while len(result) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch = (ch << 1) | 1
                lon_lo = mid
            else:
                ch <<= 1
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch = (ch << 1) | 1
                lat_lo = mid
            else:
                ch <<= 1
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            result.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(result)
