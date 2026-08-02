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


def test_feedback_endpoint_needs_a_valid_signature(monkeypatch):
    """The feedback link sits OUTSIDE the bearer-token router because a mail
    client cannot send an Authorization header. Its only protection is the
    HMAC, so that must actually be enforced — and must fail closed when no
    secret is configured."""
    from atlas.config import get_settings

    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "s3cret")
    get_settings.cache_clear()
    client = TestClient(app)

    # No token, bad token, and a token signed for the opposite vote.
    assert client.get("/feedback/1/up").status_code == 422
    assert client.get("/feedback/1/up?t=deadbeef").status_code == 403

    from atlas.report import feedback_token
    down_token = feedback_token(1, "down")
    assert client.get(f"/feedback/1/up?t={down_token}").status_code == 403
    # A bad vote word is rejected before any lookup.
    assert client.get(f"/feedback/1/sideways?t={down_token}").status_code == 400
    get_settings.cache_clear()


def test_feedback_fails_closed_with_no_secret(monkeypatch):
    from atlas.config import get_settings
    from atlas.report import feedback_token

    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "s3cret")
    get_settings.cache_clear()
    token = feedback_token(1, "up")
    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "")
    get_settings.cache_clear()

    client = TestClient(app)
    assert client.get(f"/feedback/1/up?t={token}").status_code == 403
    get_settings.cache_clear()
