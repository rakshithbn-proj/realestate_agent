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

    def fake_post(url, headers=None, json=None, timeout=None, follow_redirects=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout,
                        follow_redirects=follow_redirects)
        return FakeResponse([{"listing_id": "1"}])

    monkeypatch.setattr(fetchers.httpx, "post", fake_post)
    items = fetchers.fetch_apify({
        "actor": "thirdwatch/magicbricks-scraper",
        "input": {"searchMode": "buy", "city": "Bangalore", "maxResults": 300},
    })
    assert items == [{"listing_id": "1"}]
    # Actor path uses ~ separator; token via Bearer header; input as body
    assert "acts/thirdwatch~magicbricks-scraper/run-sync-get-dataset-items" in captured["url"]
    assert captured["headers"] == {"Authorization": "Bearer tok"}
    assert captured["json"]["city"] == "Bangalore"
    assert captured["follow_redirects"] is True


def test_token_never_appears_in_request_url(monkeypatch):
    """Regression: httpx logs the full request URL at INFO, so a ?token= would
    write the Apify token into the scheduler's logs on every daily run."""
    monkeypatch.setattr(fetchers, "get_settings",
                        lambda: SimpleNamespace(apify_token="supersecret"))
    captured = {}

    def fake_post(url, headers=None, **kw):
        captured.update(url=url, headers=headers)
        return FakeResponse([])

    monkeypatch.setattr(fetchers.httpx, "post", fake_post)
    fetchers.fetch_apify({"actor": "x/y"})
    assert "supersecret" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer supersecret"


def test_non_list_response_rejected(monkeypatch):
    monkeypatch.setattr(fetchers, "get_settings",
                        lambda: SimpleNamespace(apify_token="tok"))
    monkeypatch.setattr(fetchers.httpx, "post",
                        lambda *a, **k: FakeResponse({"error": "nope"}))
    with pytest.raises(RuntimeError, match="unexpected Apify response"):
        fetchers.fetch_apify({"actor": "x/y"})
