from types import SimpleNamespace

import pytest

import atlas.ingest.fetchers as fetchers


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_missing_token_raises(monkeypatch):
    monkeypatch.setattr(fetchers, "get_settings",
                        lambda: SimpleNamespace(apify_token=""))
    with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
        fetchers.fetch_apify({"actor": "thirdwatch/magicbricks-scraper"})


def test_actor_call_shape(monkeypatch):
    monkeypatch.setattr(fetchers, "get_settings",
                        lambda: SimpleNamespace(apify_token="tok"))
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None, follow_redirects=None):
        captured.update(url=url, params=params, json=json, timeout=timeout,
                        follow_redirects=follow_redirects)
        return FakeResponse([{"listing_id": "1"}])

    monkeypatch.setattr(fetchers.httpx, "post", fake_post)
    items = fetchers.fetch_apify({
        "actor": "thirdwatch/magicbricks-scraper",
        "input": {"searchMode": "buy", "city": "Bangalore", "maxResults": 300},
    })
    assert items == [{"listing_id": "1"}]
    # Actor path uses ~ separator; token via query param; input as body
    assert "acts/thirdwatch~magicbricks-scraper/run-sync-get-dataset-items" in captured["url"]
    assert captured["params"] == {"token": "tok"}
    assert captured["json"]["city"] == "Bangalore"
    assert captured["follow_redirects"] is True


def test_non_list_response_rejected(monkeypatch):
    monkeypatch.setattr(fetchers, "get_settings",
                        lambda: SimpleNamespace(apify_token="tok"))
    monkeypatch.setattr(fetchers.httpx, "post",
                        lambda *a, **k: FakeResponse({"error": "nope"}))
    with pytest.raises(RuntimeError, match="unexpected Apify response"):
        fetchers.fetch_apify({"actor": "x/y"})
