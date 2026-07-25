"""Legal-risk tagging v1: registry-fact vs listing-text-claim separation,
cited evidence, idempotent upsert."""
from pathlib import Path

from sqlalchemy import select

from atlas.ingest import legal, rera
from atlas.ingest.pipeline import run_source
from atlas.ingest.registry import SourceSpec
from atlas.models import Listing, ListingLegalTag

FIXTURE = Path(__file__).parent / "fixtures" / "magicbricks_sample.json"


def make_spec(path: Path) -> SourceSpec:
    return SourceSpec(name="magicbricks", city="bangalore", kind="portal",
                      fetcher="fixture", parser="magicbricks",
                      params={"path": str(path)})


def _tags(session, external_id) -> dict:
    listing = session.scalar(select(Listing).where(Listing.external_id == external_id))
    rows = session.scalars(
        select(ListingLegalTag).where(ListingLegalTag.listing_id == listing.id)
    ).all()
    return {t.item: t for t in rows}


def test_every_listing_gets_four_tags(session):
    run_source(session, make_spec(FIXTURE))
    result = legal.tag_listings(session)
    assert result.tagged_listings == 15
    assert result.tags_written == 15 * 4
    tags = _tags(session, "84945537")
    assert set(tags) == {"rera_registered", "khata_type", "jurisdiction",
                         "layout_approval"}
    for t in tags.values():
        assert t.tagger_version == "legal/1.0.0"


def test_rera_tag_is_registry_fact_when_project_present(session):
    # Ingest the RERA row that the Ramky Lumina listing (85234497) joins to
    rows = [("ACK", "PRM/KA/RERA/1251/310/PR/250304/000047", "Ramky Lumina",
             "Royaume Estates Private Limited")]
    from tests.test_rera import make_page
    rera.run(session, html_override=make_page(rows))

    run_source(session, make_spec(FIXTURE))
    legal.tag_listings(session)

    tags = _tags(session, "85234497")
    rera_tag = tags["rera_registered"]
    assert rera_tag.status == "pass"
    assert rera_tag.evidence["kind"] == "rera_registry"
    assert rera_tag.evidence["rera_reg_no"] == "PRM/KA/RERA/1251/310/PR/250304/000047"


def test_rera_id_not_in_registry_is_flagged(session):
    run_source(session, make_spec(FIXTURE))
    legal.tag_listings(session)   # no RERA rows ingested
    tags = _tags(session, "84945537")
    assert tags["rera_registered"].status == "flag"
    assert "not found" in tags["rera_registered"].detail


def test_text_claims_are_marked_unverified(session, tmp_path):
    import json
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    items[0]["description"] = "Beautiful A-khata BDA approved flat, BBMP limits."
    items[1]["description"] = "Revenue site, gram panchayat, B khata — negotiable."
    p = tmp_path / "claims.json"
    p.write_text(json.dumps(items), encoding="utf-8")

    run_source(session, make_spec(p))
    legal.tag_listings(session)

    good = _tags(session, items[0]["listing_id"])
    assert good["khata_type"].status == "pass"
    assert good["jurisdiction"].status == "pass"
    assert good["layout_approval"].status == "pass"
    # Evidence flags the claim as unverified, never as document-checked
    assert good["khata_type"].evidence["kind"] == "listing_text_claim"
    assert "NOT document-verified" in good["khata_type"].evidence["note"]

    bad = _tags(session, items[1]["listing_id"])
    assert bad["khata_type"].status == "flag"        # B-khata
    assert bad["jurisdiction"].status == "flag"      # panchayat
    assert bad["layout_approval"].status == "flag"   # revenue site


def test_no_claim_is_unknown_not_pass(session):
    run_source(session, make_spec(FIXTURE))
    legal.tag_listings(session)
    # Fixture titles carry no khata/jurisdiction language
    tags = _tags(session, "84945537")
    assert tags["khata_type"].status == "unknown"
    assert tags["jurisdiction"].status == "unknown"


def test_tagging_is_idempotent(session):
    run_source(session, make_spec(FIXTURE))
    legal.tag_listings(session)
    legal.tag_listings(session)
    tags = _tags(session, "84945537")
    # Still exactly one row per item (upsert, not insert)
    assert len(tags) == 4
