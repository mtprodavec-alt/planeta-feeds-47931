"""Scrape Planeta's sale section (https://planeta-shop.com.ua/rasprodazha).

    python tools/parse_sale.py out.json [cache_dir]

Writes one record per product card:
    {"url", "title", "origin": old price, "sale": sale price,
     "codes": model codes from the card (variant numbers), "prices": variant sale prices}
sync.py matches cards to feed offers by model code and uses the sale price as cost.
"""
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://planeta-shop.com.ua/rasprodazha"
STEP = 23  # products per page; the site pages with ?per_page=<offset>
CARD_SPLIT = re.compile(r'<article class="product-cut\b')
TITLE = re.compile(r'class="product-cut__title-link"\s+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
OLD = re.compile(r'product-price__old-value"[^>]*>([^<]*)<')
MAIN = re.compile(r'product-price__main-value"[^>]*>([^<]*)<(?:/span><span class="product-price__main-coins"[^>]*>([^<]*)<)?')
NUMBER = re.compile(r'data-product-variant--number="([^"]*)"')
VPRICE = re.compile(r'data-product-variant--price="([^"]*)"')


def fetch(offset, cache):
    path = Path(cache) / f"p{offset}.html" if cache else None
    if path and path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    url = BASE if offset == 0 else f"{BASE}?per_page={offset}"
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (feed-sync)"})
            with urlopen(req, timeout=120) as r:
                text = r.read().decode("utf-8", errors="replace")
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(10)
    if path:
        path.write_text(text, encoding="utf-8")
    return text


def num(s, coins=""):
    s = html.unescape(s or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    c = html.unescape(coins or "").replace(",", "").replace(".", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    return float(s) + (float("0." + c) if c.isdigit() else 0)


def parse(text):
    cards = []
    for chunk in CARD_SPLIT.split(text)[1:]:
        t = TITLE.search(chunk)
        old, main = OLD.search(chunk), MAIN.search(chunk)
        if not (t and old and main):
            continue
        origin, sale = num(old.group(1)), num(main.group(1), main.group(2))
        if not origin or not sale or sale >= origin:
            continue
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t.group(2)))).strip()
        codes = sorted({html.unescape(c).strip() for c in NUMBER.findall(chunk) if c.strip()})
        prices = sorted({p for p in (num(x) for x in VPRICE.findall(chunk)) if p})
        cards.append({"url": t.group(1), "title": title, "origin": origin, "sale": sale,
                      "codes": codes, "prices": prices})
    return cards


def main(dest, cache=None):
    if cache:
        Path(cache).mkdir(parents=True, exist_ok=True)
    first = fetch(0, cache)
    offsets = [int(x) for x in re.findall(r"rasprodazha\?per_page=(\d+)", first)]
    last = max(offsets) if offsets else 0
    cards = parse(first)
    for offset in range(STEP, last + STEP, STEP):
        cards += parse(fetch(offset, cache))
        if not cache:
            time.sleep(1)
    seen, unique = set(), []
    for c in cards:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
    if not unique:
        sys.exit("sale page parsed to zero cards; layout changed?")
    Path(dest).write_text(json.dumps(unique, ensure_ascii=False), encoding="utf-8")
    print(f"pages: {last // STEP + 1}, sale cards: {len(unique)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
