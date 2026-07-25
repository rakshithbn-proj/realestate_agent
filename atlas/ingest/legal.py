"""Bangalore legal-risk tagging v1 — the guardrail layer (atlas_roadmap
Appendix A; handoff §6: "crude-but-cited beats absent").

Every listing gets tagged on four items, each with a status and CITED
evidence. Two evidence classes, never conflated:

- registry facts: rera_registered joins listings.rera_ids against the
  ingested RERA registry — a verifiable cross-source fact.
- listing-text claims: khata/jurisdiction/layout keywords found in the
  listing's own title/description. A claim is evidence of what the SELLER
  says, not of legal reality — the evidence dict says so explicitly, and the
  status never goes better than 'pass (claimed)'. Document-verified checks
  live in the property-scoped legal_checks table at diligence time (Phase 3+).

Idempotent: re-tagging upserts on (listing_id, item); TAGGER_VERSION is
stamped so tag semantics changes are attributable (plan §7).
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import Listing, ListingLegalTag, ReraProject

log = logging.getLogger(__name__)

TAGGER_VERSION = "legal/1.0.0"

# (pattern, status, detail) — first match wins within an item.
_KHATA_RULES = [
    (re.compile(r"\bB[\s-]?khata\b", re.I), "flag", "B-khata claimed (restricted loans/resale)"),
    (re.compile(r"\bA[\s-]?khata\b", re.I), "pass", "A-khata claimed"),
    (re.compile(r"\be[\s-]?khata\b|\be-?aasthi\b", re.I), "pass", "e-khata/e-Aasthi claimed"),
]
_JURISDICTION_RULES = [
    (re.compile(r"\bBBMP\b", re.I), "pass", "BBMP jurisdiction claimed"),
    (re.compile(r"\bBDA\b", re.I), "pass", "BDA jurisdiction claimed"),
    (re.compile(r"\bBMRDA\b", re.I), "pass", "BMRDA jurisdiction claimed"),
    (re.compile(r"\b(?:gram(?:a)?\s+)?panchayat\b", re.I), "flag",
     "panchayat jurisdiction claimed (check approvals/tax/resale)"),
]
_LAYOUT_RULES = [
    (re.compile(r"\brevenue\s+(?:site|layout|plot|land)\b", re.I), "flag",
     "revenue site/layout claimed (unapproved — high risk)"),
    (re.compile(r"\b(?:BDA|BMRDA)[\s-]?approved\b|\bapproved\s+layout\b", re.I),
     "pass", "approved layout claimed"),
    (re.compile(r"\bDC[\s-]?converted\b|\bDC\s+conversion\b", re.I),
     "pass", "DC conversion claimed"),
]


@dataclass
class TagResult:
    tagged_listings: int
    tags_written: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _snippet(text: str, match: re.Match, radius: int = 60) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _text_claim_tag(item: str, rules, text: str) -> tuple[str, str | None, dict | None]:
    for pattern, status, detail in rules:
        m = pattern.search(text)
        if m:
            evidence = {
                "kind": "listing_text_claim",
                "note": "seller/broker claim from listing text — NOT document-verified",
                "matched": m.group(0),
                "snippet": _snippet(text, m),
            }
            return status, detail, evidence
    return "unknown", None, None


def _rera_tag(session: Session, listing: Listing,
              registry_cache: dict) -> tuple[str, str | None, dict | None]:
    if not listing.rera_ids:
        return "unknown", "no RERA id on listing", None
    for reg_no in listing.rera_ids:
        if reg_no not in registry_cache:
            project = session.scalar(
                select(ReraProject).where(ReraProject.rera_reg_no == reg_no)
            )
            registry_cache[reg_no] = project
        project = registry_cache[reg_no]
        if project is not None:
            return (
                "pass",
                f"registered: {project.project_name or reg_no}",
                {"kind": "rera_registry", "rera_project_id": project.id,
                 "rera_reg_no": reg_no, "builder_id": project.builder_id},
            )
    return (
        "flag",
        "listing carries RERA id(s) not found in the Karnataka registry",
        {"kind": "rera_registry", "unmatched_ids": list(listing.rera_ids)},
    )


def _upsert_tag(session: Session, listing_id: int, item: str, status: str,
                detail: str | None, evidence: dict | None) -> None:
    tag = session.scalar(
        select(ListingLegalTag).where(ListingLegalTag.listing_id == listing_id,
                                      ListingLegalTag.item == item)
    )
    if tag is None:
        session.add(ListingLegalTag(
            listing_id=listing_id, item=item, status=status, detail=detail,
            evidence=evidence, tagger_version=TAGGER_VERSION,
        ))
    else:
        tag.status = status
        tag.detail = detail
        tag.evidence = evidence
        tag.tagger_version = TAGGER_VERSION
        tag.checked_at = _now()


def tag_listings(session: Session, listing_ids: list[int] | None = None) -> TagResult:
    """Tag the given listings (or every listing when None)."""
    query = select(Listing)
    if listing_ids is not None:
        query = query.where(Listing.id.in_(listing_ids))
    listings = session.scalars(query).all()

    registry_cache: dict = {}
    tags_written = 0
    for listing in listings:
        text = " ".join(filter(None, (listing.title, listing.description)))

        status, detail, evidence = _rera_tag(session, listing, registry_cache)
        _upsert_tag(session, listing.id, "rera_registered", status, detail, evidence)
        tags_written += 1

        for item, rules in (("khata_type", _KHATA_RULES),
                            ("jurisdiction", _JURISDICTION_RULES),
                            ("layout_approval", _LAYOUT_RULES)):
            status, detail, evidence = _text_claim_tag(item, rules, text)
            _upsert_tag(session, listing.id, item, status, detail, evidence)
            tags_written += 1

    session.commit()
    return TagResult(tagged_listings=len(listings), tags_written=tags_written)
