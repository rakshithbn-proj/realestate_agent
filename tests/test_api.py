"""Auth gate: the API is locked without a configured token and rejects bad
tokens. (Data endpoints are exercised end-to-end in Phase 1 when they matter.)"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import atlas.auth as auth
from atlas.main import app


def test_unset_token_locks_api(monkeypatch):
    monkeypatch.setattr(auth, "get_settings",
                        lambda: SimpleNamespace(atlas_api_token=""))
    client = TestClient(app)
    assert client.get("/runs").status_code == 503


def test_missing_or_wrong_token_rejected(monkeypatch):
    monkeypatch.setattr(auth, "get_settings",
                        lambda: SimpleNamespace(atlas_api_token="sekret"))
    client = TestClient(app)
    assert client.get("/runs").status_code == 401
    assert client.get(
        "/runs", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401


def test_runs_limit_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, "get_settings",
                        lambda: SimpleNamespace(atlas_api_token="sekret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer sekret"}
    # Rejected by validation before any DB access
    assert client.get("/runs?limit=-1", headers=headers).status_code == 422
    assert client.get("/runs?limit=10000000", headers=headers).status_code == 422


def test_health_is_public():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "db" in resp.json()
