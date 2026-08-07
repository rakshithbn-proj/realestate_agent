"""The daily briefing — the Phase-2 product.

Roadmap Phase 2b sets the shape of this, and it is not "here are today's
listings". Until the cash floor is cleared the briefing's job is *the
countdown, the corridor, and who to call*; a briefing full of property that
cannot be funded trains you to stop opening it. So:

- **The capital block is printed first, on every path, including quiet days.**
  Capital is env config because it changes as you save, and a stale figure
  mis-filters the briefing in both directions — hiding what you could buy and
  surfacing what you can't. Printing the assumption daily is what makes a
  stale value visible instead of silently wrong.
- **Nothing unfundable is ever recommended.** Everything is still scored and
  watched, so a price drop into range surfaces the day it happens.
- **Quiet days still send.** Silence is indistinguishable from a dead cron.
- **The briefing states what it could not judge** — the no-data factors are
  printed by name, so the guidance-value gap the roadmap calls the core
  arbitrage signal is visibly missing rather than quietly absent.

Delivery is guarded by `report_runs.sent_at`: UNIQUE(report_date) stops a
duplicate row, not a duplicate email, and a container restart inside the send
window would otherwise re-send.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from atlas.config import get_settings
from atlas.gate import gate_status
from atlas.health import source_health_dicts
from atlas.money import inr
from atlas.models import Listing, PriceEvent, Recommendation, ReportRun
from atlas.plan import build_plan
from atlas.profile import PROFILE_VERSION, InvestorProfile, default_profile
from atlas.scoring.engine import latest_scores
from atlas.scoring.weights import NO_DATA_FACTORS, WEIGHTS, WEIGHTS_VERSION

log = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

# How many opportunities the digest will carry, and how far ahead the
# watchlist looks. Small on purpose: the roadmap's success metric is one
# genuinely interesting opportunity a WEEK, so a daily list of twenty is
# noise wearing a number.
TOP_N = 5
WATCHLIST_MONTHS = 6
WATCHLIST_N = 5


@dataclass
class Digest:
    report_date: date
    content: dict[str, Any]
    source_health: list[dict]

    @property
    def quiet(self) -> bool:
        return not self.content["opportunities"]


def _ist_today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def capital_block(profile: InvestorProfile) -> dict:
    """What the briefing assumed about your money, and which env var set it.

    Naming the env keys is the point: when a figure is wrong, the reader needs
    to know where to change it without going to the source.
    """
    return {
        "profile_version": PROFILE_VERSION,
        "deployable_inr": profile.deployable_inr,
        "reserved_inr": profile.reserved_inr,
        "committed_inr": profile.committed_inr,
        "monthly_contribution_inr": profile.monthly_contribution_inr,
        "ltv": profile.ltv,
        "ceiling_now_inr": profile.max_price_for(),
        "ceiling_cash_only_inr": profile.max_price_for(financeable=False),
        "env_keys": {
            "deployable_inr": "ATLAS_LIQUID_TOTAL_INR - ATLAS_RESERVED_INR",
            "reserved_inr": "ATLAS_RESERVED_INR",
            "committed_inr": "ATLAS_COMMITTED_INR",
            "monthly_contribution_inr": "ATLAS_MONTHLY_CONTRIBUTION_INR",
            "ltv": "ATLAS_LTV",
        },
        "note": (
            "Stamp duty and registration cannot be borrowed — they are cash at "
            "the sub-registrar. If any figure above is stale, everything below "
            "it is mis-filtered."
        ),
    }


def _recent_price_drops(session: Session, since_hours: int = 24) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    rows = session.execute(
        select(PriceEvent, Listing)
        .join(Listing, Listing.id == PriceEvent.listing_id)
        .where(PriceEvent.observed_at >= cutoff,
               PriceEvent.pct_change.isnot(None),
               PriceEvent.pct_change < 0)
        .order_by(PriceEvent.pct_change)
    ).all()
    return [{
        "listing_id": listing.id,
        "city": listing.city,
        "title": listing.title,
        "old_price_inr": event.old_price,
        "new_price_inr": event.new_price,
        "pct_change": float(event.pct_change),
        "url": listing.url,
    } for event, listing in rows]


def _new_listings(session: Session, since_hours: int = 24) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return len(session.scalars(
        select(Listing.id).where(Listing.first_seen_at >= cutoff)).all())


def build_report(session: Session, profile: InvestorProfile | None = None,
                 report_date: date | None = None) -> Digest:
    profile = profile or default_profile()
    report_date = report_date or _ist_today()

    reachable = latest_scores(session, limit=TOP_N, reachable_only=True,
                              profile=profile)
    # The watchlist is what the countdown is *for*: scored, in-corridor, and
    # reachable within the horizon — shown so a near-term target is visible
    # without ever being presented as buyable today.
    everything = latest_scores(session, limit=200, reachable_only=False,
                               profile=profile)
    reachable_ids = {row["listing_id"] for row in reachable}
    watchlist = []
    for row in everything:
        if row["listing_id"] in reachable_ids or not row["price_inr"]:
            continue
        # Affordable-today listings that merely missed the TOP_N cut belong to
        # the opportunities section, not here. The watchlist answers "what is
        # the countdown FOR", so a "0 months away" row in it is a category
        # error that makes the section read as noise.
        if row["affordable_now"]:
            continue
        months = profile.months_until_affordable(
            float(row["price_inr"]), row["financeable"])
        if months is not None and months <= WATCHLIST_MONTHS:
            watchlist.append({**row, "months_away": months})
        if len(watchlist) >= WATCHLIST_N:
            break

    plan = build_plan(session, profile)
    nearest = plan.rungs[0] if plan.rungs else None
    gate = gate_status(session)
    health = source_health_dicts(session)

    content = {
        "report_date": report_date.isoformat(),
        "weights_version": WEIGHTS_VERSION,
        "capital": capital_block(profile),
        "countdown": {
            "nearest_bar_inr": nearest.cash_needed_inr if nearest else None,
            "nearest_locality": nearest.locality if nearest else None,
            "nearest_city": nearest.city if nearest else None,
            "nearest_price_inr": nearest.price_inr if nearest else None,
            "months_away": nearest.months_away if nearest else None,
            "months_away_if_market_runs": (
                nearest.months_away_if_market_runs if nearest else None),
            "ladder": [{
                "city": r.city, "locality": r.locality,
                "price_inr": r.price_inr, "cash_needed_inr": r.cash_needed_inr,
                "months_away": r.months_away, "financeable": r.financeable,
            } for r in plan.rungs],
        },
        "opportunities": reachable,
        "watchlist": watchlist,
        "price_drops_24h": _recent_price_drops(session),
        "new_listings_24h": _new_listings(session),
        "gate": {"streak": gate.streak, "required": gate.required_days,
                 "met": gate.met},
        # Named, not omitted. The roadmap calls the guidance-value gap the core
        # arbitrage signal; a briefing that silently dropped it would imply a
        # completeness the score does not have.
        "not_scored": [
            {"factor": name, "weight": WEIGHTS.get(name, 0), "reason": reason}
            for name, reason in NO_DATA_FACTORS.items()
        ],
    }
    return Digest(report_date=report_date, content=content, source_health=health)


# --- persistence ------------------------------------------------------------

def save_report(session: Session, digest: Digest) -> ReportRun:
    """Upsert today's report row. Re-running the day replaces the content but
    never clears `sent_at` — that is what stops a second email."""
    row = session.scalar(
        select(ReportRun).where(ReportRun.report_date == digest.report_date))
    if row is None:
        row = ReportRun(report_date=digest.report_date,
                        content=digest.content,
                        source_health=digest.source_health)
        session.add(row)
        session.flush()
    else:
        row.content = digest.content
        row.source_health = digest.source_health
        row.generated_at = datetime.now(timezone.utc)

    # Recommendations are the feedback surface, so they are rewritten to match
    # what this report actually says.
    for existing in session.scalars(
        select(Recommendation).where(Recommendation.report_run_id == row.id)
    ):
        session.delete(existing)
    session.flush()

    for opportunity in digest.content["opportunities"]:
        session.add(Recommendation(
            report_run_id=row.id,
            listing_id=opportunity["listing_id"],
            tier="daily",
            headline=_headline(opportunity),
            score_id=opportunity.get("score_id"),
        ))
    session.commit()
    return row


def _headline(opportunity: dict) -> str:
    price = (f"Rs {inr(opportunity['price_inr'])}" if opportunity["price_inr"]
             else "price on request")
    return (f"[{opportunity['overall']:.0f}] "
            f"{opportunity.get('locality') or '?'}, {opportunity.get('city')} — "
            f"{opportunity.get('property_type') or '?'} {price}")


# --- feedback links ---------------------------------------------------------

def feedback_token(recommendation_id: int, vote: str) -> str:
    """HMAC over (id, vote), so a feedback link is safe without a bearer token.

    An email client cannot send an Authorization header, so the link has to
    carry its own proof. Signing the vote as well as the id means a link
    cannot be edited from a down-vote into an up-vote.
    """
    secret = get_settings().atlas_feedback_secret
    return hmac.new(secret.encode(), f"{recommendation_id}:{vote}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def verify_feedback_token(recommendation_id: int, vote: str, token: str) -> bool:
    secret = get_settings().atlas_feedback_secret
    if not secret:
        # No secret configured means no verifiable links; reject rather than
        # accept an unauthenticated write to a public endpoint.
        return False
    return hmac.compare_digest(feedback_token(recommendation_id, vote), token)


def feedback_url(recommendation_id: int, vote: str) -> str | None:
    base = get_settings().atlas_public_base_url.rstrip("/")
    if not base or not get_settings().atlas_feedback_secret:
        return None
    token = feedback_token(recommendation_id, vote)
    return f"{base}/feedback/{recommendation_id}/{vote}?t={token}"


# --- rendering --------------------------------------------------------------

def _rs(value: int | float | None) -> str:
    return f"Rs {inr(value)}" if value is not None else "-"


def render_text(digest: Digest, votes: dict[int, int] | None = None) -> str:
    """Plain-text briefing. ASCII only — this also goes into logs."""
    c = digest.content
    cap = c["capital"]
    out: list[str] = []
    a = out.append

    a(f"ATLAS DAILY - {c['report_date']}")
    a("")
    a(f"CAPITAL THIS BRIEFING ASSUMED          {cap['profile_version']}")
    a(f"  Deployable now         {_rs(cap['deployable_inr']):>16}"
      f"   ({cap['env_keys']['deployable_inr']})")
    a(f"  Reserved (untouched)   {_rs(cap['reserved_inr']):>16}")
    if cap["committed_inr"]:
        a(f"  Committed (unlockable) {_rs(cap['committed_inr']):>16}")
    a(f"  Saving                 {_rs(cap['monthly_contribution_inr']):>16} / month")
    a(f"  Ceiling today          {_rs(cap['ceiling_now_inr']):>16}"
      f"   (cash-only: {_rs(cap['ceiling_cash_only_inr'])})")
    a(f"  >> {cap['note']}")
    a("")

    cd = c["countdown"]
    a("THE COUNTDOWN")
    if cd["nearest_bar_inr"] is None:
        a("  No in-corridor priced listing to plan against yet.")
    else:
        when = ("NOW" if cd["months_away"] == 0
                else f"{cd['months_away']} months" if cd["months_away"] is not None
                else "never at this savings rate")
        a(f"  Nearest real bar   {_rs(cd['nearest_bar_inr']):>14}"
          f"   ({cd['nearest_locality']}, {cd['nearest_city']}"
          f" - {_rs(cd['nearest_price_inr'])})   {when}")
        if (cd["months_away_if_market_runs"] is not None
                and cd["months_away_if_market_runs"] != cd["months_away"]):
            a(f"  {'':>32}   {cd['months_away_if_market_runs']} months "
              "if the corridor runs +10%/yr")
    a("")

    if c["opportunities"]:
        a(f"WORTH READING TODAY  ({len(c['opportunities'])} fundable now)")
        for opportunity in c["opportunities"]:
            a("")
            a(f"  {_headline(opportunity)}")
            a(f"      cash needed {_rs(opportunity['cash_needed_inr'])}"
              f"   coverage {opportunity['coverage'] * 100:.0f}%"
              f"{'' if opportunity['financeable'] else '   [cash only - legal flag]'}")
            for factor in opportunity["factors"]:
                if factor["weight"] == 0:
                    continue
                detail = _factor_line(factor)
                a(f"      {factor['factor']:<20} "
                  f"{factor['value'] * factor['weight']:>5.1f}/{factor['weight']:<3} {detail}")
            if opportunity.get("url"):
                a(f"      {opportunity['url']}")
            vote_up = opportunity.get("feedback_up")
            vote_down = opportunity.get("feedback_down")
            if vote_up and vote_down:
                a(f"      useful? yes {vote_up}")
                a(f"              no  {vote_down}")
    else:
        a("WORTH READING TODAY")
        a("  Nothing fundable today. That is the normal state until the cash")
        a("  floor is cleared - the countdown above is the product for now.")
    a("")

    if c["watchlist"]:
        a(f"WATCHLIST  (reachable within {WATCHLIST_MONTHS} months)")
        for row in c["watchlist"]:
            a(f"  [{row['overall']:>4.0f}] {(row['locality'] or '?')[:22]:<22} "
              f"{_rs(row['price_inr']):>14}  bar {_rs(row['cash_needed_inr']):>13}"
              f"  {row['months_away']} mo")
        a("")

    if c["price_drops_24h"]:
        a("PRICE DROPS (24h)")
        for drop in c["price_drops_24h"]:
            a(f"  {(drop['title'] or '?')[:44]:<44} "
              f"{_rs(drop['old_price_inr'])} -> {_rs(drop['new_price_inr'])}"
              f"  ({drop['pct_change']:+.1f}%)")
        a("")

    a(f"NEW LISTINGS (24h)  {c['new_listings_24h']}")
    a("")
    a("SOURCES")
    for source in digest.source_health:
        mark = "ok" if source["healthy"] else "DEGRADED"
        a(f"  {source['name']}/{source['city']:<12} {mark:<9} "
          f"{source['reason']}  last={source['last_run_status']}")
    gate = c["gate"]
    a(f"GATE  {gate['streak']}/{gate['required']} clean days"
      f" - Phase 1 {'MET' if gate['met'] else 'not met'}")
    a("")
    a("NOT SCORED - no data exists")
    for item in c["not_scored"]:
        a(f"  {item['factor']:<20} {item['reason']}")
    return "\n".join(out)


def _factor_line(factor: dict) -> str:
    """One-line justification pulled from the factor's own evidence."""
    ev = factor.get("evidence") or {}
    kind = ev.get("kind")
    if kind == "abstained" or kind == "no_data":
        return "(abstained - no data)"
    if factor["factor"] == "capital_fit":
        months = ev.get("months_away")
        return ("affordable now" if months == 0
                else f"{months} months away" if months is not None
                else "savings never catch this price")
    if factor["factor"] == "legal_risk":
        return f"RERA {ev.get('rera_registered', '?')}"
    if factor["factor"] == "price_vs_locality":
        return (f"{ev.get('ratio', 1) * 100 - 100:+.0f}% vs locality median "
                f"({ev.get('comps')} comps)")
    if factor["factor"] == "distress":
        parts = []
        if ev.get("drop_from_peak_pct"):
            parts.append(f"-{ev['drop_from_peak_pct']:.0f}% off peak")
        if ev.get("days_on_market"):
            parts.append(f"{ev['days_on_market']}d on market")
        return ", ".join(parts) or "(no movement yet)"
    if factor["factor"] == "seller_motivation":
        quote = (ev.get("quote") or "")[:60]
        return f"WHY SELLING: {quote}" if quote else "(no signal)"
    if factor["factor"] == "thesis_fit":
        return f"{ev.get('asset_class', '?')}, {ev.get('corridor') or 'off-corridor'}"
    return ""


def render_html(digest: Digest) -> str:
    """HTML is the plain text in a <pre>. The briefing is dense, aligned,
    numeric content — a table layout would make it less readable, not more,
    and this renders identically in every mail client."""
    import html

    return (
        '<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:13px;line-height:1.45"><pre style="white-space:pre-wrap">'
        f"{html.escape(render_text(digest))}"
        "</pre></div>"
    )


# --- delivery ---------------------------------------------------------------

def send_via_resend(subject: str, text: str, html: str) -> bool:
    """POST to Resend. Returns False (never raises) when unconfigured.

    The key rides in an Authorization header, never a query string — httpx
    logs full request URLs at INFO, which is exactly how the Apify token
    ended up in the scheduler's logs on every run.
    """
    import httpx

    settings = get_settings()
    if not settings.resend_api_key or not settings.atlas_digest_to:
        log.warning("digest not sent: RESEND_API_KEY or ATLAS_DIGEST_TO unset")
        return False

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.atlas_digest_from,
                "to": [settings.atlas_digest_to],
                "subject": subject,
                "text": text,
                "html": html,
            },
            timeout=30,
        )
    except Exception as exc:
        log.error("digest not sent: could not reach Resend (%s)", exc)
        return False

    if response.is_error:
        # Resend puts the actual reason in the body — "you can only send to
        # your own address until a domain is verified", "invalid API key",
        # "domain not found". `raise_for_status()` discards all of that and
        # leaves you with a bare status code pointing at a generic HTTP page,
        # which is useless for the one question that matters: what do I fix?
        log.error(
            "digest not sent: Resend returned %s -- %s  (from=%r to=%r)",
            response.status_code, response.text[:500],
            settings.atlas_digest_from, settings.atlas_digest_to,
        )
        return False
    return True


def ping_healthchecks() -> None:
    """External dead-man's switch (plan.md §7). A missed ping alerts you that
    the briefing did not arrive — the delivery guarantee is enforced from
    outside, because a dead process cannot report itself dead."""
    import httpx

    url = get_settings().healthchecks_ping_url
    if not url:
        return
    try:
        httpx.get(url, timeout=10)
    except Exception:
        log.warning("healthchecks ping failed", exc_info=True)


def send_digest(session: Session, profile: InvestorProfile | None = None,
                dry_run: bool = False, force: bool = False) -> tuple[bool, str]:
    """Build, persist, and send today's briefing. Returns (sent, rendered).

    Quiet days still send: silence is indistinguishable from a dead cron, and
    a day with nothing to buy still owes you the countdown and the corridor.
    """
    digest = build_report(session, profile)
    if dry_run:
        return False, render_text(digest)

    row = save_report(session, digest)
    if row.sent_at is not None and not force:
        log.info("digest for %s already sent at %s; not re-sending",
                 digest.report_date, row.sent_at.isoformat())
        return False, render_text(digest)

    # Attach the signed feedback links now that recommendations have ids.
    recommendations = session.scalars(
        select(Recommendation).where(Recommendation.report_run_id == row.id)
    ).all()
    by_listing = {r.listing_id: r for r in recommendations}
    for opportunity in digest.content["opportunities"]:
        rec = by_listing.get(opportunity["listing_id"])
        if rec is None:
            continue
        opportunity["recommendation_id"] = rec.id
        opportunity["feedback_up"] = feedback_url(rec.id, "up")
        opportunity["feedback_down"] = feedback_url(rec.id, "down")
    # `row.content` already IS `digest.content` (same object, assigned in
    # save_report), so re-assigning it is a no-op as far as SQLAlchemy's
    # change detection is concerned and the links would never be persisted.
    # JSONB columns need the mutation flagged explicitly.
    flag_modified(row, "content")
    session.flush()

    text = render_text(digest)
    subject = _subject(digest)
    sent = send_via_resend(subject, text, render_html(digest))
    if sent:
        row.sent_at = datetime.now(timezone.utc)
        session.commit()
        ping_healthchecks()
    else:
        session.commit()
    return sent, text


def _subject(digest: Digest) -> str:
    c = digest.content
    if c["opportunities"]:
        best = c["opportunities"][0]
        return (f"Atlas {c['report_date']}: {len(c['opportunities'])} fundable"
                f" - top {best['overall']:.0f} in {best.get('locality') or '?'}")
    months = c["countdown"]["months_away"]
    if months is not None:
        return f"Atlas {c['report_date']}: nothing fundable yet - {months} months out"
    return f"Atlas {c['report_date']}: the countdown"
