"""'Why is this being sold?' — the question overall_plan.md §1 puts at the
centre of the whole system, and the one Phase 2 cannot be called done without.

The answer is in the listing's prose, so it needs a language model. Three
design rules follow from that, and each is load-bearing:

1. **Asynchronous, never inline.** Extraction runs on the Batch API (half
   price, non-interactive work — plan.md §7 LLM discipline), which answers in
   minutes to hours. Scoring runs at 07:00 and must not block on it. So
   submission and collection are separate passes over a cache table, and a
   listing without a result yet simply abstains — identical to any other
   missing datum. The factor fills in on a later day; nothing waits.

2. **A claim, never a fact.** The output is what the seller's own copy
   implies, and the evidence says so, carrying the verbatim quote so the claim
   is checkable. This is the same wall atlas/ingest/legal.py puts between the
   RERA registry join (verified) and khata keywords (claimed).

3. **The model extracts; the code judges.** Haiku returns a constrained set of
   signals; the 0-1 score is derived from those in `SIGNAL_WEIGHTS` below.
   Asking the model for a score directly would put an unversioned, unauditable
   weighting inside a prompt — exactly what atlas/scoring/weights.py exists to
   prevent.

If ANTHROPIC_API_KEY is unset every function here is a no-op and the factor
abstains for every listing. That is deliberate: a missing key must never be
indistinguishable from "no seller here is motivated".
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.config import get_settings
from atlas.models import Listing, ListingMotivation

log = logging.getLogger(__name__)

# Versioned because it judges (plan §7). Bump on ANY change to the prompt, the
# schema, the signal vocabulary, or the weights below — cached rows are keyed
# on this, so a bump re-extracts rather than mixing two vocabularies.
MOTIVATION_PROMPT_VERSION = "motivation/1.0.0"

# Haiku: extraction from short text is exactly what it is for, and at
# $1/$5 per Mtok halved by the Batch API this is cents per day over ~650
# listings. Alias rather than a dated snapshot, per the model catalogue.
MODEL = "claude-haiku-4-5"

MAX_TOKENS = 1024
# Descriptions are short; this bounds a pathological one rather than trimming
# real content. Truncation is recorded in the hash, so an extended description
# re-extracts.
MAX_CHARS = 6000

# The signal vocabulary, and what each is worth. Constrained to an enum in the
# schema so the model cannot invent a signal the weighting doesn't cover —
# which would silently score 0 and look like "not motivated".
#
# Scoring takes the MAX weight, not the sum: one genuine distress signal is the
# finding, and stacking three weak ones must not out-rank it.
SIGNAL_WEIGHTS: dict[str, float] = {
    "distress_sale": 1.00,       # explicit forced/distress sale language
    "financial_need": 1.00,      # loan repayment, medical, debt
    "urgent_sale": 0.90,         # "urgent", "immediate", "quick sale"
    "relocation": 0.70,          # moving city/country
    "inherited_property": 0.60,  # heirs selling; often negotiable
    "nri_seller": 0.60,          # remote owner, harder to hold
    "price_negotiable": 0.50,    # explicitly open to offers
    "portfolio_exit": 0.50,      # investor liquidating
}

SYSTEM_PROMPT = (
    "You extract seller-motivation signals from Indian real-estate listing "
    "text for a private investor's research notes.\n\n"
    "Report ONLY what the listing text itself states or directly implies. Do "
    "not infer motivation from price, location, or property type. Do not "
    "speculate about the seller's circumstances. If the text gives no "
    "motivation signal, return motivated=false with an empty signals list — "
    "that is the common and correct answer.\n\n"
    "The quote must be copied VERBATIM from the listing text and must be the "
    "specific phrase that carries the signal. If there is no such phrase, "
    "return an empty string."
)

# NOTE: no `minimum`/`maximum` on confidence and no `maxLength` on quote —
# numeric and string constraints are not supported by structured outputs, so
# they are enforced in code below instead of being silently dropped.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "motivated": {
            "type": "boolean",
            "description": "Does the listing text state or directly imply a "
                           "reason the seller wants to sell?",
        },
        "signals": {
            "type": "array",
            "description": "Signals present in the text. Empty if none.",
            "items": {"type": "string", "enum": sorted(SIGNAL_WEIGHTS)},
        },
        "quote": {
            "type": "string",
            "description": "Verbatim phrase from the listing carrying the "
                           "signal, or an empty string if there is none.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence that the signals are really "
                           "stated in the text.",
        },
    },
    "required": ["motivated", "signals", "quote", "confidence"],
    "additionalProperties": False,
}


class Extraction(BaseModel):
    """Typed validation before any DB write (plan §7 LLM discipline).

    Structured outputs make a schema violation unlikely, not impossible —
    a truncated response is still malformed JSON — so the response is
    revalidated here and a failure is stored as `invalid` rather than raising.
    """

    motivated: bool
    signals: list[str] = Field(default_factory=list)
    quote: str = ""
    confidence: float = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client():
    """The Anthropic client, or None when no key is configured.

    Imported lazily so the whole scoring layer stays importable — and every
    other factor keeps working — on a box that has never had a key.
    """
    key = get_settings().anthropic_api_key
    if not key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=key)


def listing_text(listing: Listing) -> str:
    """The text the extraction reads. Title first: portals often carry the
    motivation in the headline ('Urgent sale, owner relocating')."""
    parts = [p for p in (listing.title, listing.description) if p]
    return " ".join(parts).strip()[:MAX_CHARS]


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive_score(extraction: Extraction) -> float:
    """Signals -> 0-1, in code rather than in the prompt.

    Unknown signals are ignored rather than defaulted: the schema constrains
    the vocabulary, so one appearing here means the vocabulary changed without
    a version bump, and guessing a weight would hide that.
    """
    if not extraction.motivated or not extraction.signals:
        return 0.0
    weights = [SIGNAL_WEIGHTS[s] for s in extraction.signals if s in SIGNAL_WEIGHTS]
    if not weights:
        return 0.0
    confidence = min(1.0, max(0.0, extraction.confidence))
    return max(weights) * confidence


def _needs_extraction(session: Session, listing: Listing) -> bool:
    text = listing_text(listing)
    if not text:
        return False
    row = session.scalar(
        select(ListingMotivation).where(
            ListingMotivation.listing_id == listing.id,
            ListingMotivation.prompt_version == MOTIVATION_PROMPT_VERSION,
        )
    )
    if row is None:
        return True
    # Re-extract only when the text actually changed. A pending row is left
    # alone: its batch is still in flight and re-submitting would double-bill.
    return row.status != "pending" and row.source_hash != source_hash(text)


def submit_batch(session: Session, limit: int = 500) -> str | None:
    """Queue extraction for listings that need it. Returns the batch id.

    None when there is nothing to do or no API key — both are normal, and
    neither is an error.
    """
    client = _client()
    if client is None:
        log.info("motivation: ANTHROPIC_API_KEY unset; skipping extraction "
                 "(the seller_motivation factor will abstain)")
        return None

    listings = [
        listing
        for listing in session.scalars(
            select(Listing)
            .where(Listing.status.in_(("active", "relisted")))
            .order_by(Listing.last_seen_at.desc())
        )
        if _needs_extraction(session, listing)
    ][:limit]
    if not listings:
        return None

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = []
    texts: dict[int, str] = {}
    for listing in listings:
        text = listing_text(listing)
        texts[listing.id] = text
        requests.append(
            Request(
                custom_id=f"listing-{listing.id}",
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    output_config={
                        "format": {"type": "json_schema",
                                   "schema": EXTRACTION_SCHEMA}
                    },
                    messages=[{"role": "user", "content": text}],
                ),
            )
        )

    batch = client.messages.batches.create(requests=requests)
    log.info("motivation: submitted batch %s for %d listings",
             batch.id, len(requests))

    for listing in listings:
        _upsert(session, listing.id, {
            "model": MODEL,
            "source_hash": source_hash(texts[listing.id]),
            "status": "pending",
            "batch_id": batch.id,
            "requested_at": _now(),
            "extracted_at": None,
            "motivated": None,
            "score": None,
            "signals": [],
            "quote": None,
            "confidence": None,
        })
    session.commit()
    return batch.id


def _upsert(session: Session, listing_id: int, values: dict) -> None:
    row = session.scalar(
        select(ListingMotivation).where(
            ListingMotivation.listing_id == listing_id,
            ListingMotivation.prompt_version == MOTIVATION_PROMPT_VERSION,
        )
    )
    if row is None:
        row = ListingMotivation(
            listing_id=listing_id,
            prompt_version=MOTIVATION_PROMPT_VERSION,
            **values,
        )
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)


def _store_result(session: Session, listing_id: int, message: Any,
                  batch_id: str) -> None:
    """Validate one batch result and write it. Never raises on bad model
    output — a malformed answer is data ('invalid'), not a crash."""
    common = {"model": MODEL, "batch_id": batch_id, "extracted_at": _now()}

    # Check stop_reason before reading content: a refusal or a truncated
    # response has no usable body, and indexing content[0] would raise.
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        _upsert(session, listing_id, {**common, "status": "refused"})
        return
    if stop_reason == "max_tokens":
        _upsert(session, listing_id, {**common, "status": "invalid"})
        return

    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        _upsert(session, listing_id, {**common, "status": "empty"})
        return

    try:
        extraction = Extraction.model_validate_json(text)
    except ValidationError as exc:
        log.warning("motivation: invalid extraction for listing %s: %s",
                    listing_id, exc)
        _upsert(session, listing_id, {**common, "status": "invalid"})
        return

    known = [s for s in extraction.signals if s in SIGNAL_WEIGHTS]
    _upsert(session, listing_id, {
        **common,
        "status": "ok",
        "motivated": extraction.motivated,
        "score": derive_score(extraction),
        "signals": known,
        "quote": (extraction.quote or None),
        "confidence": min(1.0, max(0.0, extraction.confidence)),
    })


def collect_batches(session: Session) -> int:
    """Write back any finished batches. Returns rows updated.

    Batches that are still processing are left pending — they are collected on
    a later run, and the factor abstains until then.
    """
    client = _client()
    if client is None:
        return 0

    batch_ids = [
        b for b in session.scalars(
            select(ListingMotivation.batch_id)
            .where(ListingMotivation.status == "pending",
                   ListingMotivation.batch_id.isnot(None))
            .distinct()
        )
    ]
    updated = 0
    for batch_id in batch_ids:
        try:
            batch = client.messages.batches.retrieve(batch_id)
        except Exception:
            log.exception("motivation: could not retrieve batch %s", batch_id)
            continue
        if batch.processing_status != "ended":
            continue

        # Results come back in ANY order — key on custom_id, never position.
        for result in client.messages.batches.results(batch_id):
            try:
                listing_id = int(result.custom_id.removeprefix("listing-"))
            except ValueError:
                log.warning("motivation: unexpected custom_id %r",
                            result.custom_id)
                continue
            kind = result.result.type
            if kind == "succeeded":
                _store_result(session, listing_id, result.result.message, batch_id)
            elif kind in ("errored", "canceled", "expired"):
                # Clear `pending` so the next submit re-queues it; leaving it
                # pending would strand the listing forever.
                _upsert(session, listing_id, {
                    "model": MODEL, "batch_id": batch_id,
                    "extracted_at": _now(), "status": "invalid",
                })
            updated += 1
        session.commit()
        log.info("motivation: collected batch %s (%d results)", batch_id, updated)
    return updated


def motivations_for(session: Session,
                    listing_ids: list[int] | None = None) -> dict[int, dict]:
    """{listing_id: extraction dict} for the scorer. Only `ok` rows.

    Anything else — pending, refused, invalid, empty, absent — is simply not
    in the map, so the factor abstains rather than scoring an unread listing
    as unmotivated.
    """
    query = select(ListingMotivation).where(
        ListingMotivation.prompt_version == MOTIVATION_PROMPT_VERSION,
        ListingMotivation.status == "ok",
    )
    if listing_ids is not None:
        query = query.where(ListingMotivation.listing_id.in_(listing_ids))
    return {
        row.listing_id: {
            "status": row.status,
            "model": row.model,
            "prompt_version": row.prompt_version,
            "motivated": row.motivated,
            "score": float(row.score) if row.score is not None else 0.0,
            "signals": list(row.signals or []),
            "quote": row.quote,
            "confidence": (float(row.confidence)
                           if row.confidence is not None else None),
        }
        for row in session.scalars(query)
    }


def run(session: Session, limit: int = 500) -> tuple[int, str | None]:
    """The daily pass: collect finished work first, then queue new work.

    Collect-then-submit on purpose — it means results land before the scoring
    job reads them, instead of a day later.
    """
    collected = collect_batches(session)
    batch_id = submit_batch(session, limit=limit)
    return collected, batch_id
