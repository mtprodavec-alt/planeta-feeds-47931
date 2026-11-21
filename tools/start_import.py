"""Press PriceCreator's «Старт імпорту» for one XML source (same request the button sends).

    PRICECREATOR_UUID=... python tools/start_import.py <feed_url> <shop_id> <source_id>

Options stay off, exactly as in the manual import: «вимкнути відсутні»,
«ігнорування ієрархії», «оновити зображення». Existing cards bound to the
source are updated, only offers that are new to the source are created.
Exits non-zero if PriceCreator does not accept the request, so the run shows red.
"""
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

START = "https://pricecreator.rozetka.market/xml/start?"

feed, shop, source = sys.argv[1:4]
uuid = os.environ.get("PRICECREATOR_UUID")
if not uuid:
    sys.exit("PRICECREATOR_UUID is not set")
query = urlencode({
    "link": feed, "shopid": shop, "uuid": uuid, "sourceid": source,
    "dectivateold": "false", "clearimages": "false", "clearparent": "false",
    "cron_responce_code": "Відповідь сервера",
})
for attempt in range(3):
    try:
        req = Request(START + query, headers={"User-Agent": "feed-sync/1.0", "Accept": "application/json"})
        with urlopen(req, timeout=300) as r:
            body = r.read().decode("utf-8", "replace")
        break
    except OSError as e:
        if attempt == 2:
            sys.exit(f"import request failed: {e.__class__.__name__}")
        time.sleep(30)
try:
    data = json.loads(body)
except ValueError:
    sys.exit("unexpected answer from PriceCreator: " + body[:300])
safe = {k: v for k, v in data.items() if k not in ("uuid",)} if isinstance(data, dict) else data
print(json.dumps(safe, ensure_ascii=False)[:1500])
if not isinstance(data, dict) or not (str(data.get("id", "0")).isdigit() and int(data.get("id", 0)) > 0):
    sys.exit("PriceCreator did not start the import")
print("import started, PriceCreator job id", data["id"])
