"""Cut a small test feed out of a built feed.xml: only the given offer ids.

    python tools/subset_feed.py site/feed.xml static/test-8.xml ID1 ID2 ...
"""
import re
import sys

src, dst, *ids = sys.argv[1:]
want = set(ids)
text = open(src, encoding="utf-8").read()
blocks = [m.group(0) for m in re.finditer(r'<offer id="([^"]+)".*?</offer>\n', text, re.S)
          if m.group(1) in want]
cats = set(re.findall(r"<categoryId>([^<]+)</categoryId>", "".join(blocks)))
cat_xml = "".join(m.group(0) + "\n" for m in re.finditer(r'<category id="([^"]+)"[^>]*>[^<]*</category>', text)
                  if m.group(1) in cats)
head = text[:text.index("<categories>")]
with open(dst, "w", encoding="utf-8", newline="\n") as f:
    f.write(head + "<categories>\n" + cat_xml + "</categories><offers>\n" + "".join(blocks)
            + "</offers></shop></yml_catalog>\n")
print(f"{len(blocks)} offers, categories {sorted(cats)}")
