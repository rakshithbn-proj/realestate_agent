"""Fetchers return the raw items for a source run.

- fixture: a saved actor result on disk (tests, replay)
- apify: run an actor synchronously and return its dataset items
"""
import json
from pathlib import Path

import httpx

from atlas.config import get_settings

APIFY_BASE = "https://api.apify.com/v2"


def fetch_fixture(params: dict) -> list[dict]:
    return json.loads(Path(params["path"]).read_text(encoding="utf-8"))


def fetch_apify(params: dict) -> list[dict]:
    """Run an Apify actor and return its dataset items.

    Uses run-sync-get-dataset-items: one blocking call, items in the response.
    Validated actor + input schema in trial (handoff §7): MagicBricks returns
    ~300 items in well under the 300s default timeout.
    """
    token = get_settings().apify_token
    if not token:
        raise RuntimeError("APIFY_TOKEN is not configured")
    actor = params["actor"].replace("/", "~")
    resp = httpx.post(
        f"{APIFY_BASE}/acts/{actor}/run-sync-get-dataset-items",
        params={"token": token},
        json=params.get("input", {}),
        timeout=params.get("timeout_s", 300),
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        raise RuntimeError(f"unexpected Apify response shape: {type(items).__name__}")
    return items


FETCHERS = {
    "fixture": fetch_fixture,
    "apify": fetch_apify,
}
