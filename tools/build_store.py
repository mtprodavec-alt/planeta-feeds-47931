"""Build config/store.csv from a PriceCreator export and the commissions export.

Run locally whenever you re-export the catalog from PriceCreator:
    python tools/build_store.py catalog_47931.xlsx commissions-9808.zip config/store.csv

store.csv columns:
    offer_id     OFFERID in PriceCreator (= "Оригінальний id" the XML must carry)
    article      seller article; equals Planeta vendorCode for supplier items
    rz_category  Rozetka category id
    price        current price in the store (never go below it)
    commission   commission % from the Rozetka commissions export (before x1.08), may be empty
"""
import csv
import io
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from zipfile import ZipFile


def read_catalog(path):
    with ZipFile(path) as book:
        strings = ["".join(n.itertext()) for n in ET.fromstring(book.read("xl/sharedStrings.xml"))]
        for name in sorted(book.namelist()):
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                continue
            headers = None
            for row in ET.fromstring(book.read(name)).findall(".//{*}row"):
                values = {}
                for cell in row:
                    col = re.sub(r"\d+", "", cell.get("r"))
                    v = "".join(cell.itertext())
                    values[col] = strings[int(v)] if cell.get("t") == "s" else v
                if headers is None:
                    headers = values
                    if not {"OFFERID", "Артикул"}.issubset(headers.values()):
                        break
                    continue
                yield {headers[k]: v for k, v in values.items() if k in headers}


def clean_num(v):
    v = (v or "").strip()
    return v[:-2] if v.endswith(".0") else v


def main(catalog, commissions, out):
    rows = list(read_catalog(catalog))
    comm = {}
    if commissions:
        with ZipFile(commissions) as z, z.open("commissions.csv") as f:
            for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                comm[r["price_offer_id"]] = r["commission"].strip()
    articles = Counter(r.get("Артикул", "").strip() for r in rows)
    ids = Counter(r.get("OFFERID", "").strip() for r in rows)
    stats = Counter()
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["offer_id", "article", "rz_category", "price", "commission", "price_old"])
        for r in sorted(rows, key=lambda r: r.get("OFFERID", "")):
            oid, art = r.get("OFFERID", "").strip(), r.get("Артикул", "").strip()
            if not oid or not art:
                stats["skip: empty id/article"] += 1
                continue
            if articles[art] > 1 or ids[oid] > 1:
                stats["skip: duplicate id/article"] += 1
                continue
            w.writerow([oid, art, clean_num(r.get("ID категорії ROZETKA")),
                        r.get("Ціна", "").strip(), comm.get(oid, ""), r.get("Стара ціна", "").strip()])
            stats["written"] += 1
            stats["with commission"] += oid in comm
    # Articles we cannot map to one card: sync.py neither updates nor re-creates them.
    ambiguous = sorted({r.get("Артикул", "").strip() for r in rows
                        if r.get("Артикул", "").strip()
                        and (articles[r.get("Артикул", "").strip()] > 1 or ids[r.get("OFFERID", "").strip()] > 1)})
    with open(Path(out).with_name("ambiguous_articles.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ambiguous) + "\n")
    stats["ambiguous articles"] = len(ambiguous)
    print(dict(stats))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 3 else None, sys.argv[-1])
