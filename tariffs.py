"""Rozetka commission tariffs (xlsx from the seller cabinet) -> price bands.

The xlsx columns: A category id, B name, C brand ('-' = any), D price range
('-' = any, or 'lo-hi'), E commission %. The percent actually charged is
commission x 1.08 (confirmed against commission_sum in the API export).
"""
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal as D
from zipfile import ZipFile

CHARGE_FACTOR = D("1.08")


def load_tariffs(path):
    rules = defaultdict(dict)
    with ZipFile(path) as z:
        strings = ["".join(n.itertext()) for n in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        category = None
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).findall(".//{*}row")[1:]:
            cells = {}
            for c in row:
                value = c.findtext("{*}v")
                col = re.sub(r"\d+", "", c.get("r"))
                cells[col] = strings[int(value)] if c.get("t") == "s" and value is not None else value
            if cells.get("A"):
                category = cells["A"].removesuffix(".0")
            if not category or not cells.get("E"):
                continue
            key = ((cells.get("C") or "-").strip().casefold(), (cells.get("D") or "-").strip())
            rules[category][key] = D(cells["E"]) * CHARGE_FACTOR
    return dict(rules)


def bands(rules, brand):
    """Contiguous (lo, hi|None, effective %) bands for one category and brand."""
    brand = (brand or "").strip().casefold()
    defaults, special = {}, {}
    for (b, interval), rate in rules.items():
        if b == "-":
            defaults[interval] = rate
        elif b == brand:
            special[interval] = rate
    bounds = {0}
    grouped = []
    for mapping in (defaults, special):
        ranges = []
        for interval, rate in mapping.items():
            if interval == "-":
                continue
            lo, hi = map(int, interval.split("-"))
            bounds.update((lo, hi + 1))
            ranges.append((lo, hi, rate))
        grouped.append((mapping, ranges))
    out = []
    ordered = sorted(bounds)
    for lo, hi in zip(ordered, ordered[1:] + [None]):
        rate = None
        for mapping, ranges in grouped:
            hits = [r for a, b, r in ranges if a <= lo <= b]
            if len(hits) > 1:
                raise ValueError("overlapping bands")
            rate = hits[0] if hits else mapping.get("-", rate)
        if rate is None:
            raise ValueError("no tariff")
        out.append((lo, hi, rate))
    return out
