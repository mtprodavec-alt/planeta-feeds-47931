"""Nightly Planeta -> PriceCreator (store 47931 «Червоний жук») feed builder.

1. Downloads the 7 Planeta feeds (token from env PLANETA_FEED_TOKEN).
2. Validates them; any failure aborts the run, so yesterday's published feed stays.
3. Merges them into one set of offers keyed by Planeta vendorCode.
4. Items already in the store (config/store.csv, matched by article == vendorCode)
   go to site/stock.xml with the store OFFERID, recalculated price and stock.
5. Items not in the store go to site/new.xml (split into new-N.xml if large)
   with a stable Latin offer id and article JUK-<vendorCode>.

Price = (wholesale x k + 30 + Smart) / (0.75 - commission), rounded up,
never below the current store price (see pricing.py).
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape, quoteattr

from pricing import selling_price
from tariffs import CHARGE_FACTOR, bands, load_tariffs

FEEDS = (
    "Planeta_Sport", "Planeta_Trenirovki", "Planeta_Edinoborstva",
    "Planeta_Odezhda-i-Obuv", "Planeta_Turizm-i-Otdyh",
    "Planeta_Komandnye-vidy-sporta", "Planeta_Dlya-vseh-vidov-sporta",
)
URL = "https://planeta-shop.com.ua/mod_user_xml_link/download_feed?"
ROOT = Path(__file__).resolve().parent
MAX_DROP = 0.20            # abort if a feed shrinks by more than 20% vs last run
SPLIT_BYTES = 90 * 2**20   # keep each published file well under 100 MB
KYIV = timezone(timedelta(hours=2))


# ---------- download & parse ----------

def download(folder):
    token = os.environ.get("PLANETA_FEED_TOKEN")
    if not token:
        sys.exit("PLANETA_FEED_TOKEN is not set")
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in FEEDS:
        path = folder / f"{name}.xml"
        url = URL + urlencode({"file": path.name, "token": token})
        for attempt in range(3):
            try:
                req = Request(url, headers={"User-Agent": "feed-sync/1.0"})
                with urlopen(req, timeout=300) as r, open(path, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                break
            except OSError as e:
                if attempt == 2:
                    sys.exit(f"Download failed: {name}: {e.__class__.__name__}")
                time.sleep(30)
        print(f"downloaded {name}: {path.stat().st_size} bytes")
        paths.append(path)
    return paths


def text(el, tag):
    v = el.findtext(tag)
    return v.strip() if v else ""


def parse_param(p):
    name = (p.get("nameUA") or p.get("name") or "").strip()
    ua, ru = text(p, "valueUA"), text(p, "value")
    if not ua and not ru and p.text and p.text.strip():
        ua = ru = p.text.strip()
    return name, (p.get("name") or "").strip(), ua or ru, ru or ua


def parse_feed(path):
    root_date, cats, offers = None, {}, []
    for ev, el in ET.iterparse(path, events=("start", "end")):
        if ev == "start":
            if el.tag == "yml_catalog":
                root_date = el.get("date")
            continue
        if el.tag == "category" and el.get("id"):
            cats[el.get("id")] = (text(el, "nameUA") or text(el, "name"), el.get("parentId"))
        elif el.tag == "offer":
            costs = [text(p, "value") for p in el.findall("./prices/price") if text(p, "quantity") == "1"]
            params, seen = [], set()
            for p in el.findall("param"):
                name, name_ru, ua, ru = parse_param(p)
                if name and ua and name.casefold() not in seen:
                    seen.add(name.casefold())
                    params.append((name, name_ru, ua, ru))
            brand = next((ua for n, _, ua, _ in params if n.casefold() == "бренд"), "")
            offers.append({
                "sku": text(el, "vendorCode") or el.get("id", ""),
                "available": el.get("available") == "true",
                "qty": int(text(el, "quantity_in_stock") or 0),
                "cost": D(costs[0]) if len(costs) == 1 else None,
                "category": text(el, "categoryId"),
                "pictures": [p.text.strip() for p in el.findall("picture") if p.text and p.text.strip()],
                "name_ru": text(el, "name"), "name_ua": text(el, "nameUA"),
                "desc_ru": text(el, "description"), "desc_ua": text(el, "descriptionUA"),
                "brand": brand, "params": params, "feed": path.stem,
            })
            el.clear()
    if not offers:
        raise ValueError(f"empty feed {path.name}")
    return root_date, cats, offers


# ---------- pricing ----------

class Commission:
    def __init__(self, tariffs, store):
        self.tariffs = tariffs
        by_cat = defaultdict(list)
        for s in store.values():
            if s["commission"]:
                by_cat[s["rz_category"]].append(D(s["commission"]) * CHARGE_FACTOR)
        self.rz_max = {c: max(v) for c, v in by_cat.items()}
        self.planeta_max = {}
        observed = [r for v in by_cat.values() for r in v]
        self.global_max = max(observed) if observed else D("27") * CHARGE_FACTOR

    def learn_planeta(self, matched):
        acc = defaultdict(list)
        for offer, s, sched in matched:
            acc[offer["category"]].append(max(r for _, _, r in sched))
        self.planeta_max = {c: max(v) for c, v in acc.items()}

    def for_store(self, s, brand):
        cat = s["rz_category"]
        if cat in self.tariffs:
            try:
                return bands(self.tariffs[cat], brand), "tariff"
            except ValueError:
                pass
        if s["commission"]:
            return [(0, None, D(s["commission"]) * CHARGE_FACTOR)], "api"
        if cat in self.rz_max:
            return [(0, None, self.rz_max[cat])], "category_max"
        return [(0, None, self.global_max)], "global_max"

    def for_new(self, offer):
        if offer["category"] in self.planeta_max:
            return [(0, None, self.planeta_max[offer["category"]])], "planeta_category_max"
        return [(0, None, self.global_max)], "global_max"


# ---------- XML output ----------

def cdata(s):
    return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"


CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def offer_xml(oid, o, price, article, full=True):
    qty = o["qty"] if o["available"] else 0
    avail = "true" if qty > 0 else "false"
    out = [f'<offer id={quoteattr(oid)} available="{avail}">',
           f"<price>{price}</price>", "<currencyId>UAH</currencyId>",
           f"<categoryId>{escape(o['category'])}</categoryId>",
           f"<stock_quantity>{qty}</stock_quantity>"]
    for p in o["pictures"][:15]:
        out.append(f"<picture>{escape(p)}</picture>")
    out += [f"<vendor>{escape(o['brand'] or 'Planeta')}</vendor>",
            f"<article>{escape(article)}</article>",
            f"<name>{escape(o['name_ru'] or o['name_ua'])}</name>",
            f"<name_ua>{escape(o['name_ua'] or o['name_ru'])}</name_ua>"]
    if full:
        out += [f"<description>{cdata(o['desc_ru'] or o['desc_ua'] or o['name_ru'])}</description>",
                f"<description_ua>{cdata(o['desc_ua'] or o['desc_ru'] or o['name_ua'])}</description_ua>"]
        for name, _, ua, ru in o["params"]:
            if name.casefold() == "бренд":
                continue
            out.append(f'<param name={quoteattr(name)}><value lang="uk">{escape(ua)}</value>'
                       f'<value lang="ru">{escape(ru)}</value></param>')
    out.append("</offer>")
    return CTRL.sub("", "".join(out)) + "\n"


def write_feed(path, cats, offers_xml, now):
    head = ['<?xml version="1.0" encoding="UTF-8"?>\n',
            f'<yml_catalog date="{now:%Y-%m-%d %H:%M}"><shop>',
            "<name>Червоний жук</name><company>Червоний жук</company>",
            '<currencies><currency id="UAH" rate="1"/></currencies><categories>\n']
    used = {c for c, _ in offers_xml}
    for cid in sorted(used):
        name, parent = cats.get(cid, (cid, None))
        attr = f" parentId={quoteattr(parent)}" if parent and parent in used else ""
        head.append(f"<category id={quoteattr(cid)}{attr}>{escape(name)}</category>\n")
    head.append("</categories><offers>\n")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(head)
        f.writelines(x for _, x in offers_xml)
        f.write("</offers></shop></yml_catalog>\n")
    os.replace(tmp, path)


def write_split(site, base, cats, offers_xml, now):
    chunks, cur, size = [], [], 0
    for item in offers_xml:
        n = len(item[1].encode())
        if cur and size + n > SPLIT_BYTES:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += n
    if cur or not chunks:
        chunks.append(cur)
    names = [f"{base}.xml"] if len(chunks) == 1 else [f"{base}-{i}.xml" for i in range(1, len(chunks) + 1)]
    for name, chunk in zip(names, chunks):
        write_feed(site / name, cats, chunk, now)
    return names


VARIANT_PARAMS = ("колір", "розмір", "цвет", "размер")


def unique_name(o, names):
    """Rozetka rejects identical names in one feed: append colour/size (or sku)."""
    if names[o["name_ru"] or o["name_ua"]] <= 1:
        return o
    ua = [u for n, _, u, _ in o["params"] if n.casefold() in VARIANT_PARAMS]
    ru = [r for n, nr, _, r in o["params"] if n.casefold() in VARIANT_PARAMS or nr.casefold() in VARIANT_PARAMS]
    return {**o, "name_ru": f"{o['name_ru']} {' '.join(ru) or o['sku']}".strip(),
            "name_ua": f"{o['name_ua']} {' '.join(ua) or o['sku']}".strip()}


def new_offer_id(sku):
    return "JUK" + hashlib.sha1(sku.encode("utf-8")).hexdigest()[:10].upper()


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", type=Path, help="folder with already downloaded feeds")
    ap.add_argument("--site", type=Path, default=ROOT / "site")
    ap.add_argument("--state", type=Path, default=ROOT / "state.json",
                    help="previous run counts, used for the shrink check")
    a = ap.parse_args()

    paths = [a.feeds / f"{n}.xml" for n in FEEDS] if a.feeds else download(ROOT / ".feeds")
    try:  # previous report.json (the workflow fetches it from the live site)
        prev = json.loads(a.state.read_text(encoding="utf-8")) if a.state.exists() else {}
    except ValueError:
        prev = {}
    cats, merged, feed_info, conflicts = {}, {}, {}, 0
    for path in paths:
        date, c, offers = parse_feed(path)
        before = prev.get("feeds", {}).get(path.stem, {}).get("offers")
        if before and len(offers) < before * (1 - MAX_DROP):
            sys.exit(f"{path.stem}: {len(offers)} offers vs {before} last run, aborting")
        feed_info[path.stem] = {"date": date, "offers": len(offers)}
        cats.update(c)
        for o in offers:
            if o["sku"] in merged:
                conflicts += 1
                continue
            merged[o["sku"]] = o
        print(f"{path.stem}: {len(offers)} offers, feed date {date}")

    with open(ROOT / "config" / "store.csv", encoding="utf-8") as f:
        store_rows = list(csv.DictReader(f))
    store = {r["article"]: r for r in store_rows}
    store_ids = {r["offer_id"] for r in store_rows}
    comm = Commission(load_tariffs(ROOT / "config" / "tariff.xlsx"), {r["offer_id"]: r for r in store_rows})

    stats, sources = Counter(), Counter()
    stock_xml, new_candidates, matched = [], [], []
    names = Counter(o["name_ru"] or o["name_ua"] for o in merged.values())
    for sku, o in sorted(merged.items()):
        if o["cost"] is None or o["cost"] <= 0:
            stats["skip: no wholesale price"] += 1
            continue
        s = store.get(sku)
        if s:
            sched, src = comm.for_store(s, o["brand"])
            matched.append((o, s, sched))
            current = D(s["price"] or 0)
            price = selling_price(o["cost"], current, sched)
            sources[src] += 1
            stats["price raised" if price > current else "price kept"] += 1
            stock_xml.append((o["category"], offer_xml(s["offer_id"], unique_name(o, names), price, s["article"])))
        else:
            new_candidates.append(o)
    comm.learn_planeta(matched)

    new_xml = []
    for o in new_candidates:
        if not o["pictures"]:
            stats["new skipped: no picture"] += 1
            continue
        oid = new_offer_id(o["sku"])
        if oid in store_ids:
            stats["new skipped: id collision"] += 1
            continue
        o = unique_name(o, names)
        sched, src = comm.for_new(o)
        sources["new:" + src] += 1
        price = selling_price(o["cost"], 0, sched)
        new_xml.append((o["category"], offer_xml(oid, o, price, "JUK-" + o["sku"])))

    a.site.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KYIV)
    write_feed(a.site / "stock.xml", cats, stock_xml, now)
    new_files = write_split(a.site, "new", cats, new_xml, now)
    missing = len(store) - len(matched)
    report = {
        "generated": now.isoformat(timespec="minutes"),
        "feeds": feed_info, "supplier_offers": len(merged), "duplicates_across_feeds": conflicts,
        "store_items": len(store), "store_matched": len(matched), "store_not_in_feeds": missing,
        "new_items": len(new_xml), "files": ["stock.xml", *new_files],
        "stats": dict(stats), "commission_source": dict(sources),
    }
    (a.site / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (a.site / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Feeds 47931</title><ul>"
        + "".join(f'<li><a href="{n}">{n}</a></li>' for n in report["files"] + ["report.json"])
        + "</ul>", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
