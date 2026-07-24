from atlas.ingest.geohash import encode


def test_known_vectors():
    # Canonical geohash test vectors
    assert encode(42.6, -5.6, 5) == "ezs42"
    assert encode(57.64911, 10.40744, 11) == "u4pruydqqvj"


def test_bangalore_precision6():
    # Whitefield coords from the fixture; 6 chars ≈ 1.2km cell (blocking key)
    gh = encode(12.9680475, 77.739097, 6)
    assert len(gh) == 6
    # A point metres away falls in the same cell, a far point doesn't
    assert encode(12.9680480, 77.7390980, 6) == gh
    assert encode(13.12173909, 77.59683857, 6) != gh
