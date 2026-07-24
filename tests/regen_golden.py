"""Regenerate the golden parser output. Run, then REVIEW THE DIFF — the golden
file is a reviewed artifact, not a mirror of current behaviour."""
import json
from pathlib import Path

from atlas.ingest.parsers import magicbricks

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "magicbricks_sample.json"
GOLDEN = HERE / "golden" / "magicbricks_expected.json"

if __name__ == "__main__":
    raw_items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    parsed = [magicbricks.parse(item) for item in raw_items]
    GOLDEN.write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(f"wrote {GOLDEN} ({len(parsed)} items) — now review the diff")
