"""Build data/threads.gz: each article's discussion thread on the wiki.

A page's forum thread number only shows up on the page itself (at /<slug>/comments/show), so
this looks pages up a few at a time and remembers what it found. It is resumable: each run
fills in articles that don't have a thread yet, until its time budget runs out.

Output: {"v": 1, "t": {slug: thread_number}}

Usage: python scripts/build_threads.py [--minutes 15]
"""
import gzip
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "threads.gz"
WIKI = "https://scp-wiki.wikidot.com"
THREAD = re.compile(r'/forum/t-(\d+)(?:/([^"\'#?<\s]*))?')


def main():
    minutes = float(sys.argv[sys.argv.index("--minutes") + 1]) if "--minutes" in sys.argv else 15
    stop_at = time.time() + minutes * 60
    index = json.loads(gzip.decompress((ROOT / "data" / "index.gz").read_bytes()))
    found = json.loads(gzip.decompress(OUT.read_bytes()))["t"] if OUT.exists() else {}
    ids = [m["id"] for m in sorted(index["a"], key=lambda m: -m["r"])]   # popular pages first
    todo = [s for s in ids if s not in found]
    print(f"{len(found)} known, {len(todo)} to look up, {minutes:g} minutes")
    s = requests.Session()
    s.headers["User-Agent"] = "SCP-Field-Reader/1.0 (personal reader; github.com/xavywavy13/scp-reader)"

    def look(slug):
        if time.time() > stop_at:
            return slug, None
        for attempt in range(3):
            try:
                r = s.get(f"{WIKI}/{slug}/comments/show", timeout=30)
                if r.status_code == 200:
                    # prefer the thread named after this page
                    hits = THREAD.findall(r.text)
                    best = next((t for t, name in hits if name.lower() == slug), hits[0][0] if hits else None)
                    return slug, int(best) if best else 0
                if r.status_code == 404:
                    return slug, 0
            except requests.RequestException:
                pass
            time.sleep(3 * (attempt + 1))
        return slug, None

    done = 0
    with ThreadPoolExecutor(max_workers=3) as pool:   # about three requests a second at most
        for slug, tid in pool.map(look, todo):
            if tid:
                found[slug] = tid
            done += 1
            if done % 500 == 0:
                print(f"  {done} looked up, {len(found)} threads known")
            if time.time() > stop_at:
                break
    found = {k: v for k, v in found.items() if k in {m["id"] for m in index["a"]}}
    OUT.write_bytes(gzip.compress(json.dumps({"v": 1, "t": found}, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"{len(found)} threads known for {len(index['a'])} articles ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
