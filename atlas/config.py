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
    # Env var: APIFY_TOKEN — required for live portal scraping (Phase 1)
    apify_token: str = ""
    # Env var: ATLAS_ENABLE_SCHEDULER — in-process APScheduler jobs (VPS: on)
    atlas_enable_scheduler: bool = False
    # Active listings unseen this many days are marked removed (staleness sweep)
    stale_after_days: int = 7

    # --- Investor profile (atlas/profile.py) ---
    # Capital is config, not a constant: it changes as you save and deploy, and
    # a stale figure silently mis-filters the briefing in BOTH directions —
    # hiding what you could buy, surfacing what you can't. Env-overridable so it
    # can be corrected on the VPS without a code change and redeploy.
    # Defaults are profile-v1; bump PROFILE_VERSION when the meaning changes.
    atlas_liquid_total_inr: int = 2_500_000        # MFs + stocks + cash
    atlas_reserved_inr: int = 600_000              # emergency fund, never spent
    atlas_monthly_contribution_inr: int = 0        # saved toward the goal
    atlas_ltv: float = 0.70                        # where financing is available
    # Long-term holdings you would not normally break into, but could for an
    # exceptional deal — tracked apart from the reserve so the unlock decision
    # can be costed instead of guessed.
    atlas_committed_inr: int = 0
    atlas_committed_gain_fraction: float = 0.35    # unrealised-gain share, for LTCG


@lru_cache
def get_settings() -> Settings:
    return Settings()
