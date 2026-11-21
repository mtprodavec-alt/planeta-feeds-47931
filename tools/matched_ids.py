"""List PriceCreator internal IDs of cards that the feed updates (for bulk source binding).

    python tools/matched_ids.py catalog_export.xlsx site/feed.xml static/matched_ids.json
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_store import read_catalog  # noqa: E402

catalog, feed, out = sys.argv[1:4]
by_offer = {r["OFFERID"].strip(): int(float(r["ID"])) for r in read_catalog(catalog) if r.get("ID")}
ids = []
for _, el in ET.iterparse(feed):
    if el.tag == "offer":
        oid = el.get("id")
        if not oid.startswith("JUK") or oid.startswith("JUK-"):
            if oid in by_offer:
                ids.append(by_offer[oid])
        el.clear()
Path(out).write_text(json.dumps(sorted(set(ids))), encoding="utf-8")
print(len(set(ids)), "internal ids")
