"""The deploy snippet must actually pass the settings the app reads.

This guards a failure mode that has now happened twice, and that no other
test can see: a setting exists in `atlas/config.py`, is documented in
`.env.example`, is set correctly in the VPS `.env` — and is never passed into
the container, so the app silently uses its default.

Nothing crashes. The capital knobs in particular just make every
affordability decision against the wrong number, and the only reason it was
caught at all is that the daily briefing prints the figures it assumed.

`docker-compose.yml` (local/dev) and `deploy/compose-snippet.yml` (the VPS)
are separate files, and the VPS one is the one that matters.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = (ROOT / "deploy" / "compose-snippet.yml").read_text(encoding="utf-8")

# Settings whose absence is SILENT — the app keeps running and quietly uses a
# default. Anything that raises on startup (APIFY_TOKEN) does not need to be
# here, because a deploy that comes up wrong is its own alarm.
SILENTLY_DEFAULTING = (
    # Capital: wrong values mis-filter the briefing in both directions.
    "ATLAS_LIQUID_TOTAL_INR",
    "ATLAS_RESERVED_INR",
    "ATLAS_MONTHLY_CONTRIBUTION_INR",
    "ATLAS_LTV",
    "ATLAS_COMMITTED_INR",
    "ATLAS_COMMITTED_GAIN_FRACTION",
    # Phase 2: each degrades to a visible no-op, but only if it is passed at
    # all — an unpassed key cannot be switched on from the VPS .env.
    "ANTHROPIC_API_KEY",
    "RESEND_API_KEY",
    "ATLAS_DIGEST_TO",
    "ATLAS_FEEDBACK_SECRET",
    "ATLAS_PUBLIC_BASE_URL",
    "HEALTHCHECKS_PING_URL",
)


@pytest.mark.parametrize("key", SILENTLY_DEFAULTING)
def test_setting_is_passed_into_the_container(key):
    """The env var the app reads must appear on the left of a mapping in the
    snippet's `environment:` block — not merely be mentioned in a comment."""
    assert f"{key}:" in SNIPPET, (
        f"{key} is read by atlas/config.py but is never passed into the "
        "container by deploy/compose-snippet.yml, so setting it in the VPS "
        ".env has no effect and the app will silently use its default."
    )


def test_every_configured_setting_has_a_home():
    """Catch the reverse drift too: a new setting added to config.py that
    nobody wired up. Fails loudly listing what is missing, so the choice to
    leave something out is deliberate rather than forgotten."""
    from atlas.config import Settings

    # Deliberately not passed: assembled by compose, or dev-only.
    NOT_DEPLOYED = {
        "DATABASE_URL",        # built from the ATLAS_POSTGRES_* parts
        "TIMEZONE",            # container sets TZ instead
        "STALE_AFTER_DAYS",    # tuning knob, safe default, changing it is a code decision
    }
    expected = {name.upper() for name in Settings.model_fields} - NOT_DEPLOYED
    missing = sorted(k for k in expected if f"{k}:" not in SNIPPET)
    assert not missing, (
        "these settings are read by the app but not passed into the "
        f"container: {missing}. Add them to deploy/compose-snippet.yml, or "
        "add them to NOT_DEPLOYED here with a reason."
    )
