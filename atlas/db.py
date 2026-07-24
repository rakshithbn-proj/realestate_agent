from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from atlas.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    # Bounded connect: psycopg has no default connect timeout, so an
    # unreachable/filtered Postgres would hang callers (notably /health)
    # indefinitely instead of reporting degraded.
    return create_engine(
        url or get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
