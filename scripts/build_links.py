"""Build data/links.gz: for every article, the other articles that link to it ("Mentioned in").

Reads the article list (data/index.gz) and each article's text from its chunk, collects the
internal links the cleaner tagged with data-w="slug", and inverts them. Sources are listed
highest rated first, at most 150 per article. Pages that link to more than 100 articles
(logs, lists and indexes) are left out, since they mention everything.

Usage: python scripts/build_links.py
"""
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LINK = re.compile(r'data-w="([^"]+)"')
CAP = 150
LIST_PAGE = 100   # pages linking to more articles than this are lists or logs, not mentions


def gz_read(p):
    return json.loads(gzip.decompress(Path(p).read_bytes()))


def chunk_path(n):
    return DATA / "c" / (f"{n:03d}.gz" if n < 1000 else f"{n}.gz")


def main():
    index = gz_read(DATA / "index.gz")
    arts = {m["id"]: m for m in index["a"]}
    by_chunk = defaultdict(list)
    for m in index["a"]:
        by_chunk[m["ch"]].append(m["id"])
    back = defaultdict(set)
    for n, ids in sorted(by_chunk.items()):
        p = chunk_path(n)
        if not p.exists():
            print(f"missing chunk {p.name}")
            continue
        chunk = gz_read(p)
        for src in ids:
            dsts = {d for d in LINK.findall(chunk.get(src, "")) if d != src and d in arts}
            if len(dsts) > LIST_PAGE:
                continue
            for dst in dsts:
                back[dst].add(src)
    out = {dst: sorted(srcs, key=lambda s: -arts[s]["r"])[:CAP] for dst, srcs in back.items()}
    (DATA / "links.gz").write_bytes(gzip.compress(json.dumps({"v": 1, "b": out}, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"{sum(len(v) for v in out.values())} links into {len(out)} articles "
          f"({(DATA / 'links.gz').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
