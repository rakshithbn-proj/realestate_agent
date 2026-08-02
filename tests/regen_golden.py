"""Regenerate the golden parser output. Run, then REVIEW THE DIFF — the golden
file is a reviewed artifact, not a mirror of current behaviour."""
import json
from pathlib import Path

from atlas.ingest.parsers import SKIP, acres99, magicbricks

HERE = Path(__file__).parent

# (parse function, fixture, golden) per source.
TARGETS = (
    (magicbricks.parse,
     HERE / "fixtures" / "magicbricks_sample.json",
     HERE / "golden" / "magicbricks_expected.json"),
    (acres99.parse,
     HERE / "fixtures" / "99acres_land_sample.json",
     HERE / "golden" / "99acres_expected.json"),
)


def _encode(parsed):
    """SKIP is a sentinel object; record it as a readable marker in the golden
    file so a record silently flipping between listing and project is visible
    in the diff."""
    return "<SKIP: not a listing>" if parsed is SKIP else parsed


if __name__ == "__main__":
    for parse, fixture, golden in TARGETS:
        raw_items = json.loads(fixture.read_text(encoding="utf-8"))
        parsed = [_encode(parse(item)) for item in raw_items]
        golden.write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        print(f"wrote {golden} ({len(parsed)} items) — now review the diff")
