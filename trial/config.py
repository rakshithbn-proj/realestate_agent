"""Trial configuration — sources, volumes, paths, env settings.

One week of measurement to answer: are the free Apify actors reliable enough,
and what does all-Bangalore coverage actually cost per day?

Architecture mirrors stockquery: FastAPI + APScheduler + SQLite in Docker,
token-auth dashboard served by uvicorn.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "trial.db"
REPORTS_DIR = BASE_DIR / "reports"
TOKEN_FILE = BASE_DIR / "apify_token.txt"   # fallback if APIFY_TOKEN env not set

# --- env settings (set in .env; compose passes them through) ---
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")   # empty = auth disabled (local dev)
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")
SCRAPE_HOUR = int(os.environ.get("SCRAPE_HOUR", "6"))     # IST, daily
SCRAPE_MINUTE = int(os.environ.get("SCRAPE_MINUTE", "30"))
TIMEZONE = "Asia/Kolkata"

CITY = "Bangalore"

# Max listings per source per run. Raise after day 1-2 if cost stays negligible
# (verified test run: MagicBricks 15 items = 0.0012 compute units ≈ $0.0005).
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "300"))

# Free thirdwatch actors, consistent input schema. MagicBricks field mapping is
# verified against a real run (fixtures/magicbricks_sample.json); the other two
# use candidate-key matching — the field-coverage table on the dashboard shows
# immediately if a mapping needs adjusting after the first real run.
SOURCES = {
    "magicbricks": {
        "actor": "thirdwatch/magicbricks-scraper",
        "input": {"searchMode": "buy", "city": CITY, "maxResults": MAX_RESULTS},
    },
    # NoBroker's actor defaults ownerOnly=true, which the trial was inheriting
    # implicitly — that is why it returns ~25 items against maxResults=300 while
    # MagicBricks returns 300. The 25 are real, distinct, owner-direct listings
    # (verified: 50 raw rows over 2 runs, 25 distinct ids, 100% parseable), not a
    # cap or a failure. Owner-direct is the higher-signal subset for acquisition
    # (no broker in between, seller contactable, `negotiable` flag present), so
    # keep it — but state it explicitly rather than relying on the default.
    # Set ownerOnly=False to widen to agent listings for comps/market coverage.
    "nobroker": {
        "actor": "thirdwatch/nobroker-scraper",
        "input": {"searchMode": "buy", "city": CITY, "maxResults": MAX_RESULTS,
                  "ownerOnly": True},
    },
}

# Official public registries collected directly (trial/sources/), no actor and
# no API key in the path. Listed separately from SOURCES because the Apify
# scrape loop must not try to run them as actors — but monitoring, the report
# and the dashboard treat every source the same, via ALL_SOURCES.
OFFICIAL_SOURCES = ["rera"]

ALL_SOURCES = list(SOURCES) + OFFICIAL_SOURCES

# --- Parked sources -------------------------------------------------------
# acres99 (thirdwatch/acres99-scraper): PARKED 2026-07-20, day 1 of the trial.
# The actor reports SUCCEEDED and pushes 300 items per run, but 596 of 600 raw
# items archived were bare stubs echoing the input config
# ({"searchMode", "gated_community", "verified", "self_verified"}) with no
# listing_id and no property data — 0.7% usable. Not a field-mapping problem:
# there is nothing in the payload to map. Apify still advertises 88.6% success
# for it because that measures run completion, not data quality — precisely the
# silent failure ANOMALY_UNPARSED_RATIO exists to catch, and it fired on both runs.
# Actor was last modified 2026-07-16, four days before the trial started.
#
# Parked rather than swapped mid-trial: changing sources now would contaminate
# the cost/reliability baseline the 7-day trial is measuring. After day 7,
# re-test as a clean one-off, in this order:
#   1. thirdwatch/acres99-scraper again (the author may fix it)
#   2. fatihtahta/99acres-scraper-ppe ($3.5/1k, 98.5% success) — plan.md §4 fallback
# Worth restoring: 99acres is the only one of the three whose input schema
# supports propertyType="plot", which matters for the land / JD thesis.
PARKED_SOURCES = {
    "acres99": {
        "actor": "thirdwatch/acres99-scraper",
        "input": {"searchMode": "buy", "city": CITY, "maxResults": MAX_RESULTS},
    },
}

# If a run returns fewer than this fraction of the source's trailing average,
# treat the run as anomalous: record it, but do NOT mark missing listings as
# removed (protects days-on-market data from partial scrapes).
ANOMALY_VOLUME_RATIO = 0.5

# If more than this fraction of a run's items fail to normalize (no external_id
# extracted — e.g. the actor pushed empty/stub records), mark the run anomalous
# even though the actor itself reported SUCCEEDED. Caught live: acres99-scraper
# returned "SUCCEEDED, 300 items" while 298 of them were bare stubs
# ({"searchMode": "buy", "gated_community": false, ...}) with no listing_id —
# a silent data-quality collapse that status=ok alone would not surface.
ANOMALY_UNPARSED_RATIO = 0.3

# Apify approximate price per compute unit (USD) — fallback when the run object
# doesn't report usageTotalUsd directly.
USD_PER_COMPUTE_UNIT = 0.4
