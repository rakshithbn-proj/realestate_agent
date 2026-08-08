"""HTML rendering for the daily briefing.

Split out of report.py because email markup has nothing to do with what the
briefing decides, and because the first version was wrong in a way worth
naming: it wrapped the terminal output in a `<pre>`. That is a developer
looking at their own tool, not a person reading their inbox on a phone at 7am.

Three constraints shape everything here:

- **Email clients are not browsers.** Layout is tables, styles are inline,
  and there is no flexbox, grid, or external stylesheet. Gmail strips `<head>`
  styles; Outlook renders through Word. Anything clever breaks somewhere.
- **A briefing is scanned, not read.** One number should answer "how far am I"
  before any scrolling. Everything else is supporting detail.
- **Nothing internal leaks.** Source-file citations, table names and roadmap
  phases belong in `atlas.cli score`, not in the product. What the score could
  not judge is stated in one plain line, because that honesty is the point —
  the essay explaining it is not.
"""
import html
from typing import Any

from atlas.money import compact, inr

# A restrained palette. Dark text on white survives every client's dark-mode
# inversion better than a designed dark theme, which tends to get inverted
# into something unreadable.
INK = "#111827"          # near-black, body text
MUTED = "#6b7280"        # secondary text
FAINT = "#9ca3af"        # labels
RULE = "#e5e7eb"         # hairlines
CANVAS = "#f6f7f9"       # page behind the card
CARD = "#ffffff"
ACCENT = "#15803d"       # green: reachable, healthy
WARN = "#b45309"         # amber: legal flag, degraded source

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")


def esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _section(title: str, body: str) -> str:
    return f"""
      <tr><td style="padding:28px 28px 0 28px">
        <div style="font:600 11px/1 {FONT};letter-spacing:.09em;
                    text-transform:uppercase;color:{FAINT};padding-bottom:12px">
          {esc(title)}</div>
        {body}
      </td></tr>"""


def _capital_strip(cap: dict) -> str:
    """The capital the briefing assumed — first, always (roadmap Phase 2b).

    Compact rather than a block: it must be visible before anything is
    recommended, because a stale figure mis-filters everything below it, but
    leading an email with six config lines buries the actual news.
    """
    cells = [("Deployable", inr(cap["deployable_inr"])),
             ("Saving/mo", inr(cap["monthly_contribution_inr"])),
             ("Ceiling", inr(cap["ceiling_now_inr"]))]
    if cap.get("committed_inr"):
        cells.append(("Unlockable", inr(cap["committed_inr"])))
    tds = "".join(
        f"""<td style="padding:0 18px 0 0;white-space:nowrap">
              <div style="font:400 10px/1.4 {FONT};color:{FAINT};
                          letter-spacing:.05em;text-transform:uppercase">{esc(label)}</div>
              <div style="font:600 15px/1.5 {FONT};color:{INK}">Rs {esc(value)}</div>
            </td>"""
        for label, value in cells)
    return f"""
      <tr><td style="padding:18px 28px;background:#fbfbfc;
                     border-bottom:1px solid {RULE}">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0">
          <tr>{tds}</tr>
        </table>
        <div style="font:400 11px/1.6 {FONT};color:{FAINT};padding-top:10px">
          Assumed by this briefing. If any figure is stale, everything below is
          filtered against the wrong number. Stamp duty and registration cannot
          be borrowed &mdash; they are cash.
        </div>
      </td></tr>"""


def _hero(countdown: dict) -> str:
    """The one number: how far away the first deal is."""
    if countdown["nearest_bar_inr"] is None:
        return _section("The countdown", f"""
        <div style="font:400 15px/1.6 {FONT};color:{MUTED}">
          No in-corridor priced listing to plan against yet.
        </div>""")

    months = countdown["months_away"]
    if months == 0:
        headline, sub = "Reachable now", "You clear the cash bar on this today."
    elif months is None:
        headline = "Not on this savings rate"
        sub = "The market is moving faster than the savings. Buy smaller or further out."
    else:
        headline = f"{months} months away"
        sub = "until the cash bar is cleared at the current savings rate"

    runs = countdown.get("months_away_if_market_runs")
    caveat = (f"<div style=\"font:400 12px/1.6 {FONT};color:{FAINT};padding-top:6px\">"
              f"{runs} months if the corridor runs +10%/yr</div>"
              if runs is not None and runs != months else "")

    return f"""
      <tr><td style="padding:26px 28px 22px 28px">
        <div style="font:700 30px/1.2 {FONT};color:{INK}">{esc(headline)}</div>
        <div style="font:400 14px/1.6 {FONT};color:{MUTED};padding-top:4px">
          {esc(sub)}</div>
        {caveat}
        <table role="presentation" cellpadding="0" cellspacing="0" border="0"
               style="margin-top:16px;border:1px solid {RULE};border-radius:8px">
          <tr>
            <td style="padding:12px 16px;border-right:1px solid {RULE}">
              <div style="font:400 10px/1.4 {FONT};color:{FAINT};
                          letter-spacing:.05em;text-transform:uppercase">Cash needed</div>
              <div style="font:600 17px/1.5 {FONT};color:{INK}">
                Rs {esc(inr(countdown['nearest_bar_inr']))}</div>
            </td>
            <td style="padding:12px 16px">
              <div style="font:400 10px/1.4 {FONT};color:{FAINT};
                          letter-spacing:.05em;text-transform:uppercase">Nearest entry</div>
              <div style="font:600 14px/1.5 {FONT};color:{INK}">
                {esc(countdown['nearest_locality'] or '?')},
                {esc((countdown['nearest_city'] or '').title())}
                &middot; Rs {esc(inr(countdown['nearest_price_inr']))}</div>
            </td>
          </tr>
        </table>
      </td></tr>"""


# Reader-facing labels. The internal factor names are for `score --explain`.
_FACTOR_LABEL = {
    "legal_risk": "Legal",
    "capital_fit": "Affordability",
    "price_vs_locality": "Price vs area",
    "thesis_fit": "Fit to your thesis",
    "distress": "Seller pressure",
    "seller_motivation": "Why selling",
}


def _reasons(opportunity: dict, factor_line) -> str:
    """The three strongest reasons, in plain words.

    Only the top contributors: a full six-factor decomposition is an audit
    trail, and the place for that is `atlas.cli score --explain`.
    """
    scored = [f for f in opportunity["factors"]
              if f["weight"] > 0 and f.get("evidence", {}).get("kind")
              not in ("abstained", "no_data")]
    scored.sort(key=lambda f: f["value"] * f["weight"], reverse=True)
    rows = []
    for f in scored[:3]:
        detail = factor_line(f)
        if not detail:
            continue
        rows.append(f"""
          <tr>
            <td style="padding:3px 10px 3px 0;font:400 12px/1.6 {FONT};
                       color:{FAINT};white-space:nowrap;vertical-align:top">
              {esc(_FACTOR_LABEL.get(f['factor'], f['factor']))}</td>
            <td style="padding:3px 0;font:400 12px/1.6 {FONT};color:{INK}">
              {esc(detail)}</td>
          </tr>""")
    if not rows:
        return ""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" style="margin-top:10px">{"".join(rows)}</table>')


def _button(href: str, label: str, primary: bool = False) -> str:
    bg, fg, border = ((INK, "#ffffff", INK) if primary
                      else ("#ffffff", MUTED, RULE))
    return (f'<a href="{esc(href)}" style="display:inline-block;'
            f'padding:8px 14px;margin:0 6px 0 0;border-radius:6px;'
            f'background:{bg};color:{fg};border:1px solid {border};'
            f'font:600 12px/1 {FONT};text-decoration:none">{esc(label)}</a>')


def _opportunity_card(opportunity: dict, factor_line) -> str:
    price = (f"Rs {inr(opportunity['price_inr'])}" if opportunity["price_inr"]
             else "Price on request")
    flag = ("" if opportunity["financeable"] else
            f'<span style="display:inline-block;margin-left:8px;padding:2px 7px;'
            f'border-radius:4px;background:#fef3c7;color:{WARN};'
            f'font:600 10px/1.6 {FONT}">CASH ONLY &mdash; LEGAL FLAG</span>')

    actions = []
    if opportunity.get("url"):
        actions.append(_button(opportunity["url"], "View listing", primary=True))
    if opportunity.get("feedback_up"):
        actions.append(_button(opportunity["feedback_up"], "Useful"))
    if opportunity.get("feedback_down"):
        actions.append(_button(opportunity["feedback_down"], "Not useful"))
    action_row = (f'<div style="padding-top:14px">{"".join(actions)}</div>'
                  if actions else "")

    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             border="0" style="margin-bottom:12px;border:1px solid {RULE};
                               border-radius:10px;background:{CARD}">
        <tr><td style="padding:16px 18px">
          <table role="presentation" width="100%" cellpadding="0"
                 cellspacing="0" border="0">
            <tr>
              <td style="vertical-align:top">
                <div style="font:600 16px/1.4 {FONT};color:{INK}">
                  {esc(opportunity.get('locality') or '?')},
                  {esc((opportunity.get('city') or '').title())}{flag}</div>
                <div style="font:400 13px/1.6 {FONT};color:{MUTED};padding-top:2px">
                  {esc(opportunity.get('property_type') or 'Property')}
                  &middot; {esc(price)}
                  &middot; needs Rs {esc(inr(opportunity['cash_needed_inr']))} cash</div>
              </td>
              <td width="46" style="vertical-align:top;text-align:right">
                <div style="display:inline-block;min-width:38px;padding:6px 0;
                            border-radius:8px;background:#ecfdf5;
                            font:700 17px/1 {FONT};color:{ACCENT};
                            text-align:center">
                  {opportunity['overall']:.0f}</div>
              </td>
            </tr>
          </table>
          {_reasons(opportunity, factor_line)}
          {action_row}
        </td></tr>
      </table>"""


def _watchlist(rows: list[dict]) -> str:
    trs = "".join(f"""
      <tr>
        <td style="padding:7px 0;border-bottom:1px solid {RULE};
                   font:400 13px/1.5 {FONT};color:{INK}">
          {esc(r.get('locality') or '?')}
          <span style="color:{FAINT}">&middot; {esc(compact(r['price_inr']))}</span></td>
        <td style="padding:7px 0;border-bottom:1px solid {RULE};text-align:right;
                   font:400 13px/1.5 {FONT};color:{MUTED};white-space:nowrap">
          Rs {esc(inr(r['cash_needed_inr']))}
          <span style="color:{FAINT}">&middot; {r['months_away']} mo</span></td>
      </tr>""" for r in rows)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{trs}</table>'


def _ladder(rungs: list[dict]) -> str:
    """The cheapest real entry points and what each requires.

    Shown when nothing is fundable yet. Every row carries its distance in
    months, so it reads as a target rather than an offer.
    """
    trs = []
    for r in rungs:
        when = ("now" if r["months_away"] == 0
                else f"{r['months_away']} mo" if r["months_away"] is not None
                else "not at this rate")
        near = r["months_away"] is not None and r["months_away"] <= 12
        flag = ("" if r["financeable"] else
                f'<span style="color:{WARN}"> &middot; cash only</span>')
        trs.append(f"""
      <tr>
        <td style="padding:9px 0;border-bottom:1px solid {RULE};
                   font:400 13px/1.5 {FONT};color:{INK}">
          {esc(r.get('locality') or '?')}
          <span style="color:{FAINT}">&middot; {esc((r.get('city') or '').title())}
          &middot; {esc(compact(r['price_inr']))}</span>{flag}</td>
        <td style="padding:9px 0;border-bottom:1px solid {RULE};text-align:right;
                   font:400 13px/1.5 {FONT};color:{MUTED};white-space:nowrap">
          Rs {esc(inr(r['cash_needed_inr']))}
          <span style="color:{ACCENT if near else FAINT};font-weight:600">
            &middot; {esc(when)}</span></td>
      </tr>""")
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             border="0">{"".join(trs)}</table>
      <div style="font:400 12px/1.7 {FONT};color:{FAINT};padding-top:12px">
        Nothing is fundable today &mdash; normal until the cash bar is cleared.
        These are targets, not recommendations.
      </div>"""


def _price_drops(drops: list[dict]) -> str:
    trs = "".join(f"""
      <tr>
        <td style="padding:7px 0;border-bottom:1px solid {RULE};
                   font:400 13px/1.5 {FONT};color:{INK}">
          {esc((d.get('title') or '?')[:60])}</td>
        <td style="padding:7px 0;border-bottom:1px solid {RULE};text-align:right;
                   font:400 13px/1.5 {FONT};color:{ACCENT};white-space:nowrap">
          {d['pct_change']:+.1f}%
          <span style="color:{FAINT}">&middot; Rs {esc(inr(d['new_price_inr']))}</span></td>
      </tr>""" for d in drops[:8])
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{trs}</table>'


def _footer(digest, content: dict) -> str:
    """System health in one line, and one honest line about the score's gaps.

    The gaps used to be three paragraphs of internal citations. They are real
    and worth stating — a score of 79 should not read as complete — but the
    reasoning lives in `atlas.cli score`, not in someone's inbox.
    """
    unhealthy = [s for s in digest.source_health if not s["healthy"]]
    if unhealthy:
        names = ", ".join(f"{s['name']}/{s['city']}" for s in unhealthy)
        health = (f'<span style="color:{WARN};font-weight:600">'
                  f'{esc(len(unhealthy))} source(s) degraded: {esc(names)}</span>')
    else:
        health = (f'<span style="color:{ACCENT}">All '
                  f'{len(digest.source_health)} sources healthy</span>')

    gate = content["gate"]
    gate_txt = (f"Phase 1 met &middot; {gate['streak']}/{gate['required']} clean days"
                if gate["met"] else
                f"{gate['streak']}/{gate['required']} clean collection days")
    missing = ", ".join(_FACTOR_LABEL.get(i["factor"], i["factor"]).lower()
                        for i in content["not_scored"])

    return f"""
      <tr><td style="padding:22px 28px 28px 28px;border-top:1px solid {RULE}">
        <div style="font:400 12px/1.7 {FONT};color:{MUTED}">
          {health} &middot; {gate_txt} &middot;
          {content['new_listings_24h']} new listings today
        </div>
        <div style="font:400 11px/1.7 {FONT};color:{FAINT};padding-top:8px">
          Not yet factored into any score: guidance values, infrastructure
          proximity, rental yield. Scores are relative rankings, not appraisals.
        </div>
      </td></tr>"""


def render(digest, factor_line) -> str:
    """The full email. `factor_line` comes from report.py so the plain-text and
    HTML parts explain a factor with exactly the same words."""
    c = digest.content
    blocks = [_capital_strip(c["capital"]), _hero(c["countdown"])]

    if c["opportunities"]:
        cards = "".join(_opportunity_card(o, factor_line)
                        for o in c["opportunities"])
        blocks.append(_section(
            f"Worth reading today &middot; {len(c['opportunities'])} fundable now",
            cards))
    elif c["countdown"]["ladder"]:
        # See report.py: an email that only says "nothing yet" is a daily
        # reminder that nothing changed. The ladder gives the reader something
        # concrete to aim at, with every row stating how far away it is so it
        # can never be mistaken for a recommendation.
        blocks.append(_section("What you are saving toward",
                               _ladder(c["countdown"]["ladder"][:6])))
    else:
        blocks.append(_section("Worth reading today", f"""
        <div style="padding:14px 16px;border:1px dashed {RULE};border-radius:8px;
                    font:400 13px/1.7 {FONT};color:{MUTED}">
          No in-corridor priced listing to plan against yet.
        </div>"""))

    if c["watchlist"]:
        blocks.append(_section("Next up &middot; within 6 months",
                               _watchlist(c["watchlist"])))
    if c["price_drops_24h"]:
        blocks.append(_section("Price drops today",
                               _price_drops(c["price_drops_24h"])))
    blocks.append(_footer(digest, c))

    return f"""<!--[if mso]><style>body,table,td{{font-family:Arial,sans-serif!important}}</style><![endif]-->
<div style="background:{CANVAS};padding:24px 12px;font-family:{FONT}">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         border="0" style="max-width:620px;margin:0 auto;background:{CARD};
                           border-radius:14px;overflow:hidden;
                           border:1px solid {RULE}">
    <tr><td style="padding:22px 28px 16px 28px;border-bottom:1px solid {RULE}">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font:700 17px/1.2 {FONT};color:{INK};letter-spacing:-.01em">
            Atlas</td>
          <td style="text-align:right;font:400 12px/1.2 {FONT};color:{FAINT}">
            {esc(c['report_date'])}</td>
        </tr>
      </table>
    </td></tr>
    {"".join(blocks)}
  </table>
</div>"""
