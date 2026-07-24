from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Env var: DATABASE_URL
    database_url: str = "postgresql+psycopg://atlas:atlas@127.0.0.1:5432/atlas"
    # Env var: ATLAS_API_TOKEN — empty means the API rejects everything but /health
    atlas_api_token: str = ""
    # All scheduled jobs run in this timezone (plan.md §7)
    timezone: str = "Asia/Kolkata"


@lru_cache
def get_settings() -> Settings:
    return Settings()
