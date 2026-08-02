"""Parser registry and the parser contract.

A parser takes one raw item and returns:

- a listings-shaped ``dict`` — a normal listing;
- ``None`` — a genuine parse FAILURE (no usable id); the pipeline counts it
  against the unparsed ratio, because a feed that stops parsing is how a
  scraper dies quietly;
- ``SKIP`` — a valid record that is deliberately not a listing.

`SKIP` exists because 99acres mixes individual resale listings with builder
*projects*, and a project is not a purchasable unit. Returning `None` for
those would count every project as a parse failure and, once projects are a
large enough share of a feed, push the run past ANOMALY_UNPARSED_RATIO — which
would mark healthy runs `anomalous` and freeze the staleness sweep, since the
sweep only trusts sources with a recent `ok` run. A skip is a decision, not a
failure, and the pipeline counts it separately.
"""
from atlas.ingest.parsers import acres99, magicbricks


class _Skip:
    """Sentinel: a valid record that is not a listing."""

    __slots__ = ()

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return "SKIP"

    def __bool__(self) -> bool:
        # Falsy so `if not parsed` still reads naturally, while `is SKIP`
        # remains the explicit test the pipeline uses.
        return False


SKIP = _Skip()

# parser key -> (parse function, version stamp)
PARSERS = {
    "magicbricks": (magicbricks.parse, magicbricks.PARSER_VERSION),
    "acres99": (acres99.parse, acres99.PARSER_VERSION),
}
