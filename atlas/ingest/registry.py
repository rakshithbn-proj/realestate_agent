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
    # Declared but not collected. Two reasons this exists, both real:
    # a paid source should be reviewable in code before it starts billing, and
    # the Phase-1 gate requires every *enabled* source to land an `ok` run
    # daily — so switching one on mid-streak puts the clock at the mercy of a
    # brand-new scraper. Disabled sources are skipped by the daily job and,
    # because sources.enabled is written from here, ignored by the gate.
    enabled: bool = True


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
    # --- The plot source (handoff §3 finding 1) ------------------------------
    # MagicBricks returns no land at all, so until this runs the land/JD
    # thesis has no inventory behind it and the corridors were chosen for an
    # asset class Atlas cannot see.
    #
    # Corridor-targeted, not city-wide, and that is a cost decision as much as
    # a signal one: `limit` is PER LOCATION, so these seven seeds at 40 each
    # are ~280 results/day (~$0.98/day at $0.00349/result). A plain
    # "Bangalore" search costs about the same and spends most of it on
    # Whitefield villas far above the ceiling. Capping at inr_1_crore keeps
    # the spend on stock near the reachable band — with the caveat that the
    # land median feeding price_vs_locality is then *this* comparison set,
    # not a market census.
    #
    # SHIPPED DISABLED. Enable only once the Phase-1 gate reads 7/7: a newly
    # enabled source must land an `ok` run every day from its first one, so
    # switching this on mid-streak bets the clock on a scraper that has never
    # run in production.
    "acres99_land": SourceSpec(
        name="99acres",
        city="bangalore",
        kind="portal",
        fetcher="apify",
        parser="acres99",
        enabled=False,
        params={
            "actor": "fatihtahta/99acres-scraper-ppe",
            "input": {
                "location": ["Attibele", "Sarjapur", "Chandapura",
                             "Electronic City", "Devanahalli", "Hoskote",
                             "Whitefield"],
                "deal_type": "residential_sale",
                "property_type": ["land"],
                "max_price": "inr_1_crore",
                "limit": 40,          # per location
            },
        },
    ),
    # Mysore at a lower limit: thinner market, and the thesis there is still
    # being tested rather than acted on (handoff §4a).
    "acres99_land_mysore": SourceSpec(
        name="99acres",
        city="mysore",
        kind="portal",
        fetcher="apify",
        parser="acres99",
        enabled=False,
        params={
            "actor": "fatihtahta/99acres-scraper-ppe",
            "input": {
                "location": ["Mysore"],
                "deal_type": "residential_sale",
                "property_type": ["land"],
                "max_price": "inr_1_crore",
                "limit": 60,
            },
        },
    ),
}


def enabled_sources() -> dict[str, SourceSpec]:
    """Sources the daily job should actually collect."""
    return {name: spec for name, spec in SOURCES.items() if spec.enabled}
