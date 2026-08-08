"""The daily briefing.

Its failure modes are all about trust in a thing you read every morning:
recommending property you cannot fund, going silent on a bad day (which is
indistinguishable from a dead cron), sending twice, leaking an API key into a
URL, or quietly dropping the capital assumption that decides what gets shown
at all.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from atlas import report as R
from atlas.config import get_settings
from atlas.models import (
    Listing,
    ListingLegalTag,
    Locality,
    PriceEvent,
    Recommendation,
    ReportRun,
    Source,
)
from atlas.profile import profile_with
from atlas.scoring.engine import score_listings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Settings are lru_cached; these tests set env vars."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _listing(session, external_id, price, locality="Sarjapur Road",
             city="bangalore", property_type="apartment", area=1200.0,
             title=None, posted_at=None):
    source = session.scalar(select(Source))
    if source is None:
        source = Source(name="test", city=city, kind="portal", fetcher="fixture")
        session.add(source)
        session.flush()
    loc = session.scalar(select(Locality).where(
        Locality.city == city, Locality.name == locality))
    if loc is None:
        loc = Locality(city=city, name=locality)
        session.add(loc)
        session.flush()
    listing = Listing(
        source_id=source.id, external_id=external_id, status="active",
        title=title or f"Listing {external_id}", city=city,
        locality_id=loc.id, property_type=property_type, area_sqft=area,
        price_inr=price, lister_kind="owner", posted_at=posted_at,
        parser_version="test/1.0.0", rera_ids=[])
    session.add(listing)
    session.flush()
    for item, status in (("rera_registered", "pass"), ("khata_type", "unknown"),
                         ("jurisdiction", "unknown"), ("layout_approval", "unknown")):
        session.add(ListingLegalTag(listing_id=listing.id, item=item,
                                    status=status, evidence={"kind": "test"},
                                    tagger_version="test/1.0.0"))
    session.flush()
    return listing


def _profile():
    # Roughly the real position: ~Rs 3.5L accessible, saving Rs 75k/month.
    return profile_with(liquid_total_inr=4_100_000, reserved_inr=600_000,
                        monthly_contribution_inr=75_000)


def _scored(session, **kwargs):
    listing = _listing(session, **kwargs)
    session.commit()
    score_listings(session, profile=_profile())
    return listing


# --- the capital block ------------------------------------------------------

def test_capital_block_is_present_on_every_path(session):
    """Phase 2b's requirement and the reason the digest exists in this shape:
    capital is env config, and a stale figure mis-filters in both directions.
    Printing the assumption daily is what makes it visible."""
    digest = R.build_report(session, _profile())
    assert digest.quiet                       # no listings at all
    text = R.render_text(digest)
    assert "CAPITAL THIS BRIEFING ASSUMED" in text
    # First section, before anything else.
    assert text.index("CAPITAL THIS BRIEFING") < text.index("THE COUNTDOWN")


def test_capital_block_matches_the_profile_and_names_its_env_keys(session):
    profile = _profile()
    digest = R.build_report(session, profile)
    cap = digest.content["capital"]
    assert cap["deployable_inr"] == profile.deployable_inr == 3_500_000
    assert cap["monthly_contribution_inr"] == 75_000
    assert cap["ceiling_now_inr"] == profile.max_price_for()
    # Naming the env var is the point: the reader must know where to fix it.
    assert "ATLAS_RESERVED_INR" in R.render_text(digest)
    assert "cannot be borrowed" in cap["note"]


def test_capital_block_survives_a_quiet_day(session):
    digest = R.build_report(session, _profile())
    text = R.render_text(digest)
    assert "Deployable now" in text
    assert "Nothing fundable today" in text
    # A quiet day is normal until the floor is cleared, and must say so.
    assert "countdown above is the product" in text


# --- what may and may not be recommended ------------------------------------

def test_unfundable_listing_is_never_recommended(session):
    """Phase 2b done-when: never surface a property that cannot be funded on
    the date it is shown."""
    _scored(session, external_id="huge", price=90_000_000)
    digest = R.build_report(session, _profile())
    assert digest.content["opportunities"] == []
    R.save_report(session, digest)
    assert session.scalars(select(Recommendation)).all() == []


def test_fundable_listing_is_recommended_with_its_reasons(session):
    """Reasons are given in the reader's words, not the internal factor keys —
    `legal_risk` and `capital_fit` are for `score --explain`, whose audience is
    auditing the number rather than acting on it."""
    _scored(session, external_id="cheap", price=1_500_000)
    digest = R.build_report(session, _profile())
    assert len(digest.content["opportunities"]) == 1
    text = R.render_text(digest)
    assert "WORTH READING TODAY" in text
    assert "Legal:" in text and "Affordability:" in text
    assert "needs Rs" in text
    # No internal identifiers leak into the product surface.
    assert "legal_risk" not in text
    assert "capital_fit" not in text


def test_only_the_strongest_reasons_are_shown(session):
    """Six factors per listing is an audit trail. An email gets the top three,
    or the reader stops reading."""
    _scored(session, external_id="cheap", price=1_500_000)
    digest = R.build_report(session, _profile())
    text = R.render_text(digest)
    shown = sum(1 for label in R.FACTOR_LABEL.values() if f"{label}:" in text)
    assert 0 < shown <= 3


def test_watchlist_shows_near_term_targets_without_calling_them_buyable(session):
    # cash bar Rs 36.6L against Rs 35L deployable — just out of reach today,
    # a few months of saving away.
    _scored(session, external_id="soon", price=10_000_000)
    digest = R.build_report(session, _profile())
    assert digest.content["opportunities"] == []
    watch = digest.content["watchlist"]
    assert len(watch) == 1
    assert 0 < watch[0]["months_away"] <= R.WATCHLIST_MONTHS
    assert "WATCHLIST" in R.render_text(digest)


def test_watchlist_never_contains_something_affordable_today(session):
    """A '0 months away' row here is a category error: the watchlist answers
    'what is the countdown for', so anything already fundable belongs in the
    opportunities section even when it missed the top-N cut."""
    for i in range(R.TOP_N + 3):
        _listing(session, external_id=f"cheap-{i}", price=1_000_000 + i * 1000)
    session.commit()
    score_listings(session, profile=_profile())

    digest = R.build_report(session, _profile())
    assert len(digest.content["opportunities"]) == R.TOP_N
    assert all(row["months_away"] > 0 for row in digest.content["watchlist"])


def test_briefing_states_what_it_could_not_judge(session):
    """A 79 must not read as complete. But the reasoning behind each gap is
    developer detail — it belongs in `atlas.cli score`, not in an inbox, and
    the email says it in one plain line instead of three cited paragraphs."""
    digest = R.build_report(session, _profile())
    names = {row["factor"] for row in digest.content["not_scored"]}
    assert "guidance_value_gap" in names          # still carried in the data

    text = R.render_text(digest)
    assert "guidance values" in text
    assert "not appraisals" in text
    # The internal citations must never reach the reader.
    for leak in ("handoff", "atlas_roadmap", "PostGIS", "searchMode",
                 "guidance_value_gap", "§"):
        assert leak not in text, f"internal detail leaked into the briefing: {leak}"


def test_price_drops_and_source_health_are_carried(session):
    listing = _scored(session, external_id="drop", price=1_500_000)
    session.add(PriceEvent(listing_id=listing.id, old_price=1_800_000,
                           new_price=1_500_000, pct_change=-16.7))
    session.commit()
    digest = R.build_report(session, _profile())
    assert len(digest.content["price_drops_24h"]) == 1
    assert digest.source_health          # the "is the scraper dead?" line
    text = R.render_text(digest)
    assert "PRICE DROPS" in text
    # Health collapses to one summary line — the reader needs "is anything
    # broken", and the per-source detail lives in `atlas.cli health`. (The
    # fixture's source has no runs, so it correctly reports as degraded.)
    assert "DEGRADED" in text
    assert "new listings today" in text
    assert "clean" in text          # the gate streak


def test_a_degraded_source_is_named_not_buried(session, monkeypatch):
    """The one health case that must never be quiet: 'no new listings' and
    'the scraper is dead' have to look different."""
    digest = R.build_report(session, _profile())
    digest.source_health = [
        {"name": "magicbricks", "city": "bangalore", "healthy": False,
         "reason": "2 consecutive bad runs", "last_run_status": "failed"},
        {"name": "rera_karnataka", "city": "karnataka", "healthy": True,
         "reason": "ok", "last_run_status": "ok"},
    ]
    text = R.render_text(digest)
    assert "DEGRADED" in text
    assert "magicbricks/bangalore" in text
    assert "magicbricks/bangalore" in R.render_html(digest)


# --- persistence and the double-send guard ----------------------------------

def test_same_day_rerun_leaves_one_report_row(session):
    _scored(session, external_id="cheap", price=1_500_000)
    R.save_report(session, R.build_report(session, _profile()))
    R.save_report(session, R.build_report(session, _profile()))
    assert len(session.scalars(select(ReportRun)).all()) == 1
    # ...and one recommendation, not two.
    assert len(session.scalars(select(Recommendation)).all()) == 1


def test_digest_is_not_sent_twice(session, monkeypatch):
    """UNIQUE(report_date) stops a duplicate ROW, not a duplicate EMAIL. A
    container restart inside the send window would otherwise re-send."""
    _scored(session, external_id="cheap", price=1_500_000)
    sends = []
    monkeypatch.setattr(R, "send_via_resend",
                        lambda *a, **k: sends.append(a) or True)

    sent_first, _ = R.send_digest(session, _profile())
    sent_again, _ = R.send_digest(session, _profile())

    assert sent_first is True
    assert sent_again is False
    assert len(sends) == 1
    assert session.scalar(select(ReportRun)).sent_at is not None


def test_force_allows_a_deliberate_resend(session, monkeypatch):
    _scored(session, external_id="cheap", price=1_500_000)
    sends = []
    monkeypatch.setattr(R, "send_via_resend",
                        lambda *a, **k: sends.append(a) or True)
    R.send_digest(session, _profile())
    R.send_digest(session, _profile(), force=True)
    assert len(sends) == 2


def test_quiet_day_still_sends(session, monkeypatch):
    """Silence is indistinguishable from a dead cron."""
    sends = []
    monkeypatch.setattr(R, "send_via_resend",
                        lambda *a, **k: sends.append(a) or True)
    sent, _ = R.send_digest(session, _profile())
    assert sent is True
    assert len(sends) == 1


def test_dry_run_stores_nothing_and_sends_nothing(session, monkeypatch):
    _scored(session, external_id="cheap", price=1_500_000)
    monkeypatch.setattr(R, "send_via_resend",
                        lambda *a, **k: pytest.fail("dry run must not send"))
    sent, text = R.send_digest(session, _profile(), dry_run=True)
    assert sent is False
    assert "ATLAS DAILY" in text
    assert session.scalar(select(ReportRun)) is None


def test_failed_delivery_does_not_mark_the_day_as_sent(session, monkeypatch):
    """Otherwise a transient Resend outage silently costs a day's briefing."""
    _scored(session, external_id="cheap", price=1_500_000)
    monkeypatch.setattr(R, "send_via_resend", lambda *a, **k: False)
    sent, _ = R.send_digest(session, _profile())
    assert sent is False
    assert session.scalar(select(ReportRun)).sent_at is None


# --- delivery mechanics -----------------------------------------------------

def test_resend_key_rides_in_a_header_never_a_url(session, monkeypatch):
    """The exact bug already fixed once in the Apify fetcher: httpx logs full
    request URLs at INFO, so a query-string key lands in the logs every day."""
    captured = {}

    class _Response:
        is_error = False
        status_code = 200

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Response()

    monkeypatch.setenv("RESEND_API_KEY", "re_secret_key")
    monkeypatch.setenv("ATLAS_DIGEST_TO", "me@example.com")
    get_settings.cache_clear()
    monkeypatch.setattr("httpx.post", fake_post)

    assert R.send_via_resend("subject", "text", "<p>html</p>") is True
    assert captured["headers"]["Authorization"] == "Bearer re_secret_key"
    assert "re_secret_key" not in captured["url"]
    assert captured["url"] == R.RESEND_ENDPOINT


def test_unconfigured_delivery_is_a_logged_no_op_not_a_crash(session):
    assert R.send_via_resend("s", "t", "<p>h</p>") is False


def test_html_escapes_portal_text(session):
    """Listing titles are portal-controlled text that reaches the email body
    via the price-drop section."""
    listing = _scored(session, external_id="x", price=1_500_000,
                      title="Plot <script>alert(1)</script>")
    session.add(PriceEvent(listing_id=listing.id, old_price=1_800_000,
                           new_price=1_500_000, pct_change=-16.7))
    session.commit()
    digest = R.build_report(session, _profile())
    assert "<script>" in R.render_text(digest)      # raw in the text part
    html = R.render_html(digest)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- feedback links ---------------------------------------------------------

def test_feedback_token_is_rejected_when_tampered(monkeypatch):
    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "s3cret")
    get_settings.cache_clear()
    good = R.feedback_token(7, "up")
    assert R.verify_feedback_token(7, "up", good)
    # Signing the VOTE too means a link cannot be edited into the opposite one.
    assert not R.verify_feedback_token(7, "down", good)
    assert not R.verify_feedback_token(8, "up", good)
    assert not R.verify_feedback_token(7, "up", "deadbeef")


def test_feedback_is_rejected_outright_with_no_secret_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "")
    get_settings.cache_clear()
    # Must not fall open: the endpoint is unauthenticated by design.
    assert not R.verify_feedback_token(7, "up", R.feedback_token(7, "up"))


def test_feedback_links_are_omitted_when_not_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "")
    monkeypatch.setenv("ATLAS_PUBLIC_BASE_URL", "")
    get_settings.cache_clear()
    assert R.feedback_url(1, "up") is None


def test_feedback_links_are_attached_to_sent_recommendations(session, monkeypatch):
    monkeypatch.setenv("ATLAS_FEEDBACK_SECRET", "s3cret")
    monkeypatch.setenv("ATLAS_PUBLIC_BASE_URL", "https://atlas.example.com")
    get_settings.cache_clear()
    _scored(session, external_id="cheap", price=1_500_000)
    monkeypatch.setattr(R, "send_via_resend", lambda *a, **k: True)

    R.send_digest(session, _profile())
    row = session.scalar(select(ReportRun))
    opportunity = row.content["opportunities"][0]
    assert opportunity["feedback_up"].startswith("https://atlas.example.com/feedback/")
    assert "/up?t=" in opportunity["feedback_up"]
    assert "/down?t=" in opportunity["feedback_down"]


# --- the subject line -------------------------------------------------------

def test_subject_says_whether_anything_is_fundable(session):
    quiet = R._subject(R.build_report(session, _profile()))
    assert "nothing fundable" in quiet or "countdown" in quiet
    _scored(session, external_id="cheap", price=1_500_000)
    loud = R._subject(R.build_report(session, _profile()))
    assert "fundable" in loud


def test_resend_rejection_is_logged_with_its_reason_not_a_traceback(
        session, monkeypatch, caplog):
    """Resend explains the real problem in the response body — "you can only
    send to your own address until a domain is verified", "invalid API key".
    raise_for_status() throws that away and leaves a bare 403 pointing at a
    generic HTTP page, which answers none of the questions the reader has.

    A delivery failure must also stay recoverable: log it, return False, leave
    sent_at null so tomorrow retries — never crash the command or the job."""
    import httpx

    class _Response:
        status_code = 403
        is_error = True
        text = ('{"statusCode":403,"message":"You can only send testing '
                'emails to your own email address (owner@example.com)."}')

    monkeypatch.setenv("RESEND_API_KEY", "re_key")
    monkeypatch.setenv("ATLAS_DIGEST_TO", "someone-else@example.com")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response())

    assert R.send_via_resend("s", "t", "<p>h</p>") is False
    assert "403" in caplog.text
    assert "your own email address" in caplog.text     # the actionable part
    assert "someone-else@example.com" in caplog.text   # and what it tried


def test_unreachable_resend_does_not_crash_the_job(session, monkeypatch, caplog):
    import httpx

    monkeypatch.setenv("RESEND_API_KEY", "re_key")
    monkeypatch.setenv("ATLAS_DIGEST_TO", "me@example.com")
    get_settings.cache_clear()

    def boom(*a, **k):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "post", boom)
    assert R.send_via_resend("s", "t", "<p>h</p>") is False
    assert "could not reach Resend" in caplog.text
