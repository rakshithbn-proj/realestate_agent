import pytest

from atlas import jobs


def test_ingest_portal_rejects_unknown_source():
    with pytest.raises(KeyError, match="unknown portal"):
        jobs.ingest_portal("zillow")


def test_ingest_portal_runs_only_the_named_source(monkeypatch):
    calls = []

    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(jobs, "_session", lambda: FakeSession())
    monkeypatch.setattr(jobs, "run_source",
                        lambda session, spec: calls.append(spec.name) or
                        _Result())
    jobs.ingest_portal("magicbricks")
    assert calls == ["magicbricks"]


class _Result:
    run_id = 1
    status = "ok"
    items_found = 0
    new = updated = price_changed = relisted = failed = 0
