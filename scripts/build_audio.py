"""Build data/audio.gz: audio adaptations for each article, from the wiki's Audio Adaptations page.

https://scp-wiki.wikidot.com/audio-adaptations lists articles in tables: the first cell links the
article, the second lists recordings (YouTube, SoundCloud, podcasts...) with the narrator's
name as the link text. Output: {"v": 1, "built": date, "a": {slug: [[narrator, url], ...]}}

Usage: python scripts/build_audio.py            (reads the wiki)
       python scripts/build_audio.py --dump DIR (reads the scp-data dump's hubs, for testing)
"""
import gzip
import json
import re
import sys
import time
from pathlib import Path

import lxml.html

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "audio.gz"
PAGE = "https://scp-wiki.wikidot.com/audio-adaptations"
WS = re.compile(r"\s+")


def slug_of(href):
    m = re.match(r"^(?:https?://(?:www\.)?(?:scp-wiki\.wikidot\.com|scpwiki\.com))?/([^/#?:]+)/?$", (href or "").strip())
    return m.group(1).lower() if m else None


def load():
    if "--dump" in sys.argv:
        hubs = json.load(open(Path(sys.argv[sys.argv.index("--dump") + 1]) / "hubs" / "index.json"))
        return lxml.html.fromstring(hubs["audio-adaptations"]["raw_content"])
    import requests
    r = requests.get(PAGE, timeout=60, headers={"User-Agent": "SCP-Field-Reader/1.0 (personal reader; github.com/xavywavy13/scp-reader)"})
    r.raise_for_status()
    doc = lxml.html.fromstring(r.text)
    pc = doc.get_element_by_id("page-content", None)
    return pc if pc is not None else doc


def main():
    index = json.loads(gzip.decompress((ROOT / "data" / "index.gz").read_bytes()))
    ids = {m["id"] for m in index["a"]}
    root = load()
    out = {}
    for tr in root.xpath(".//tr"):
        cells = tr.xpath("./td")
        if len(cells) < 2:
            continue
        a = cells[0].xpath(".//a[@href]")
        slug = slug_of(a[0].get("href")) if a else None
        if not slug or slug not in ids:
            continue
        recs = []
        for link in cells[1].xpath(".//a[@href]"):
            url = link.get("href").strip()
            if not url.startswith("http"):
                continue
            url = url.replace("://www.youtu.be/", "://youtu.be/")
            name = WS.sub(" ", link.text_content()).strip() or "Recording"
            if [name, url] not in recs:
                recs.append([name[:60], url])
        if recs:
            out.setdefault(slug, [])
            out[slug] += [r for r in recs if r not in out[slug]]
    data = {"v": 1, "built": time.strftime("%Y-%m-%d"), "a": out}
    OUT.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"{sum(len(v) for v in out.values())} recordings for {len(out)} articles ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
