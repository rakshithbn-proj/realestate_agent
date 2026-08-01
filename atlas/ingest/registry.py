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


# Production sources (Mysore is one config entry away by design — handoff §4a).
# Tests register fixture-backed specs directly. Actor + input schema validated
# in the trial (trial/config.py): 300 items/run, ~$0.0005 in compute.
SOURCES: dict[str, SourceSpec] = {
    "magicbricks": SourceSpec(
        name="magicbricks",
        city="bangalore",
        kind="portal",
        fetcher="apify",
        parser="magicbricks",
        params={
            "actor": "thirdwatch/magicbricks-scraper",
            "input": {"searchMode": "buy", "city": "Bangalore", "maxResults": 300},
        },
        expected_daily_volume=300,
    ),
    # Mysore: an early-stage market whose thesis is meant to be tested with
    # data, not assumed (handoff §4a). Sources are keyed on (name, city), so
    # this shares the bangalore spec's fetcher/parser and gets its own source
    # row, run history, and health line. Volume is a fraction of Bangalore's —
    # expected_daily_volume is left unset until a trailing average exists,
    # since a wrong expectation would mislabel a normal thin day.
    "magicbricks_mysore": SourceSpec(
        name="magicbricks",
        city="mysore",
        kind="portal",
        fetcher="apify",
        parser="magicbricks",
        params={
            "actor": "thirdwatch/magicbricks-scraper",
            "input": {"searchMode": "buy", "city": "Mysore", "maxResults": 300},
        },
    ),
}
