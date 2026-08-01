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


def test_gate_requires_auth(monkeypatch):
    """The gate reports collection history — behind the token like /runs."""
    monkeypatch.setattr(auth, "get_settings",
                        lambda: SimpleNamespace(atlas_api_token="sekret"))
    client = TestClient(app)
    assert client.get("/gate").status_code == 401


def test_gate_reports_streak(session, engine, monkeypatch):
    from pathlib import Path

    import atlas.main as main
    from atlas.ingest.pipeline import run_source
    from atlas.ingest.registry import SourceSpec

    run_source(session, SourceSpec(
        name="magicbricks", city="bangalore", kind="portal",
        fetcher="fixture", parser="magicbricks",
        params={"path": str(Path(__file__).parent / "fixtures"
                            / "magicbricks_sample.json")}))
    monkeypatch.setattr(auth, "get_settings",
                        lambda: SimpleNamespace(atlas_api_token="sekret"))
    # The endpoint calls get_engine(), which reads DATABASE_URL from .env — the
    # DEVELOPER's database, not this test's. Without this the test asserts
    # against whatever happens to be in the dev DB and fails outright when the
    # local Postgres isn't running.
    monkeypatch.setattr(main, "get_engine", lambda: engine)

    client = TestClient(app)
    body = client.get("/gate", headers={"Authorization": "Bearer sekret"}).json()
    assert body["required_days"] == 7
    assert body["streak"] == 1          # exactly the one run created above
    assert body["met"] is False
    assert body["days"][-1]["sources"] == {"magicbricks/bangalore": "ok"}


def test_health_is_public():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "db" in resp.json()
