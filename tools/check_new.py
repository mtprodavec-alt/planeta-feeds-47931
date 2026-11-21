"""Decide whether PriceCreator must import new cards.

    python tools/check_new.py prev_new_ids.txt site/new_ids.txt

Prints how many offer ids appeared since the previous published run and writes
`has_new=true|false` to $GITHUB_OUTPUT. A missing previous list counts as "all new".
"""
import os
import sys
from pathlib import Path


def read(path):
    p = Path(path)
    return {x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()} if p.exists() else set()


prev, cur = read(sys.argv[1]), read(sys.argv[2])
added = sorted(cur - prev)
print(f"new offer ids now: {len(cur)}, previous run: {len(prev)}, appeared since: {len(added)}")
for oid in added[:20]:
    print("  +", oid)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"has_new={'true' if added else 'false'}\n")
