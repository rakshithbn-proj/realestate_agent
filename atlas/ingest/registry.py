"""Source specs: what to fetch, how to parse it, which market it belongs to."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceSpec:
    name: str            # sources.name, e.g. 'magicbricks'
    city: str            # market slug: 'bangalore' | 'mysore' (handoff §4a)
    kind: str            # portal | official | news
    fetcher: str         # key into fetchers.FETCHERS
    parser: str          # key into parsers.PARSERS
    params: dict = field(default_factory=dict)
    expected_daily_volume: int | None = None


# Production sources (Phase 1 wires the Apify fetcher; Mysore is one config
# entry away by design). Tests register fixture-backed specs directly.
SOURCES: dict[str, SourceSpec] = {
    "magicbricks": SourceSpec(
        name="magicbricks",
        city="bangalore",
        kind="portal",
        fetcher="apify",
        parser="magicbricks",
        params={"actor": "thirdwatch/magicbricks-scraper", "city": "Bangalore"},
        expected_daily_volume=300,
    ),
}
