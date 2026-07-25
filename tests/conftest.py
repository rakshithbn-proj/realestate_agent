import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]

# Tables the ingestion layer writes; TRUNCATE ... CASCADE handles FK order.
SPINE_TABLES = ("raw_payloads", "price_events", "listing_versions",
                "listing_legal_tags", "listings", "scrape_runs", "sources",
                "localities", "rera_projects", "builders")


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    """Postgres for the e2e tests, in preference order:
    1. ATLAS_TEST_DATABASE_URL (CI service container, or your own instance)
    2. embedded pgserver (Linux/macOS)
    3. portable binaries via tests/_local_pg.py (Windows — see its docstring)
    """
    url = os.environ.get("ATLAS_TEST_DATABASE_URL")
    if url:
        yield url
        return

    try:
        import pgserver
    except ImportError:
        pgserver = None
    if pgserver is not None:
        server = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata")))
        try:
            yield server.get_uri().replace("postgresql://", "postgresql+psycopg://")
        finally:
            server.cleanup()
        return

    from tests._local_pg import LocalPostgres, find_pg_bin

    bindir = find_pg_bin()
    if bindir is None:
        pytest.skip(
            "no Postgres available: set ATLAS_TEST_DATABASE_URL, install "
            "pgserver, or drop portable binaries in .pgbin/ (tests/_local_pg.py)"
        )
    server = LocalPostgres(bindir, tmp_path_factory.mktemp("pgdata"))
    try:
        yield server.start()
    finally:
        server.stop()


@pytest.fixture(scope="session")
def engine(database_url):
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    # configparser interpolation: escape any % in the URL
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with engine.begin() as conn:
        conn.execute(text(
            f"TRUNCATE {', '.join(SPINE_TABLES)} RESTART IDENTITY CASCADE"
        ))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    yield s
    s.close()
