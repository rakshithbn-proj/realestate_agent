"""RERA collector: parser against the real archived registry page (432KB gz
from the trial), plus DB round-trips on a small synthetic page."""
import gzip
from pathlib import Path

import pytest
from sqlalchemy import func, select

from atlas.ingest import rera
from atlas.models import Builder, RawPayload, ReraProject

PAGE_GZ = Path(__file__).parent / "fixtures" / "rera_page.html.gz"


@pytest.fixture(scope="module")
def real_page() -> str:
    with gzip.open(PAGE_GZ, "rt", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_parse_real_registry_page(real_page):
    items = rera.parse(real_page)
    # ~9.8k rows total, ~8.8k registered (handoff §7)
    assert len(items) > 9000
    registered = [i for i in items if i["reg_no"].strip()]
    unregistered = [i for i in items if not i["reg_no"].strip()]
    assert len(registered) > 8000
    assert len(unregistered) > 0          # in-flight applications exist
    for rec in registered[:100]:
        assert rec["reg_no_canon"].startswith("PRM/KA/RERA/")
    # Promoter normalisation dedups (casing/suffix variants collapse)
    display_names = {i["promoter_name"] for i in registered if i["promoter_name"]}
    norm_names = {i["promoter_norm"] for i in registered if i["promoter_norm"]}
    assert len(norm_names) < len(display_names)


def test_norm_promoter():
    assert rera.norm_promoter("Sobha Limited") == rera.norm_promoter("SOBHA LIMITED")
    assert rera.norm_promoter("ESS & ESS Builders Pvt Ltd") == \
           rera.norm_promoter("ESS AND ESS BUILDERS PRIVATE LIMITED")
    assert rera.norm_promoter("Acme Constructions Pvt Ltd India") == "ACME CONSTRUCTIONS"
    assert rera.norm_promoter("  ") is None
    assert rera.norm_promoter(None) is None


def test_canon_reg_no():
    assert rera.canon_reg_no("TOR/PRM/KA/RERA/1251/310/PR/250304/000047") == \
           "PRM/KA/RERA/1251/310/PR/250304/000047"
    assert rera.canon_reg_no(" prm/ka/rera/1/2/pr/3 ") == "PRM/KA/RERA/1/2/PR/3"
    # No recognisable number: uppercased-stripped, not silently None
    assert rera.canon_reg_no("something-else") == "SOMETHING-ELSE"
    assert rera.canon_reg_no(None) is None


def make_page(rows: list[tuple[str, str, str, str]]) -> str:
    parts = []
    for ack, reg, name, promoter in rows:
        for var, val in zip(("applicationNameList", "applicationNameList2",
                             "applicationNameList3", "applicationNameList4"),
                            (ack, reg, name, promoter)):
            parts.append(f"{var}.push('{val}');")
    return "<html><script>" + "\n".join(parts) + "</script></html>"


SMALL_ROWS = [
    ("ACK1", "PRM/KA/RERA/1251/446/PR/000001", "Alpha Heights", "Sobha Limited"),
    ("ACK2", "PRM/KA/RERA/1251/446/PR/000002", "Beta Meadows", "SOBHA LIMITED"),
    # Same reg no the MagicBricks fixture carries with a TOR/ prefix:
    ("ACK3", "PRM/KA/RERA/1251/310/PR/250304/000047", "Ramky Lumina",
     "Royaume Estates Private Limited"),
    ("ACK4", "", "Gamma In Flight", "Delta Developers"),   # unregistered
]


def test_parse_unescapes_html_entities():
    page = make_page([("A", "PRM/KA/RERA/1/1/PR/1", "P", "ESS &amp; ESS LLP")])
    rec = rera.parse(page)[0]
    assert rec["promoter_name"] == "ESS & ESS LLP"
    assert rec["promoter_norm"] == "ESS AND ESS"


def test_parse_length_mismatch_fails_loudly():
    page = make_page(SMALL_ROWS) + "applicationNameList.push('EXTRA');"
    with pytest.raises(ValueError, match="length mismatch"):
        rera.parse(page)


def test_run_upserts_projects_and_dedups_builders(session):
    result = rera.run(session, html_override=make_page(SMALL_ROWS))

    assert result.status == "ok"
    assert result.items_found == 4
    assert result.new == 3
    assert result.unregistered == 1        # in-flight application skipped

    assert session.scalar(select(func.count(ReraProject.id))) == 3
    # Two Sobha casing variants collapse to ONE builder + Royaume = 2
    assert session.scalar(select(func.count(Builder.id))) == 2
    sobha = session.scalar(select(Builder).where(Builder.name_norm == "SOBHA"))
    assert sobha is not None

    # Raw page archived before parsing
    raw = session.scalar(select(RawPayload))
    assert raw.payload_text and "applicationNameList" in raw.payload_text

    # Idempotent re-run: updates, no duplicates
    result2 = rera.run(session, html_override=make_page(SMALL_ROWS))
    assert result2.new == 0
    assert result2.updated == 3
    assert session.scalar(select(func.count(ReraProject.id))) == 3
    assert session.scalar(select(func.count(Builder.id))) == 2


def test_run_records_parse_failure(session):
    result = rera.run(session, html_override="<html>not the registry</html>")
    assert result.status == "failed"
    # The raw page is still archived — recoverable, not lost
    assert session.scalar(select(func.count(RawPayload.id))) == 1
