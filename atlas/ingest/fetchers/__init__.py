"""Fetchers return the raw items for a source run.

Phase 0 ships the fixture fetcher (a saved actor result on disk) so the
pipeline can be proven end-to-end. The Apify fetcher lands in Phase 1.
"""
import json
from pathlib import Path


def fetch_fixture(params: dict) -> list[dict]:
    return json.loads(Path(params["path"]).read_text(encoding="utf-8"))


def fetch_apify(params: dict) -> list[dict]:
    raise NotImplementedError("Apify fetcher lands in Phase 1 (M1 collector)")


FETCHERS = {
    "fixture": fetch_fixture,
    "apify": fetch_apify,
}
