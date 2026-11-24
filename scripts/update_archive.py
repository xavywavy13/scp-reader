"""Bring the app's archive up to date with the latest scp-data dump.

Usage:
  python scripts/update_archive.py <scp-api/docs/data/scp> [--full]

Weekly update (default):
  - every article's details are refreshed: rating, title, nickname, tags, object class,
    series and author
  - new articles are added
  - articles that were noticeably rewritten (word count changed by 15% and 150+ words)
    get their new text
  - articles removed from the wiki are dropped
  New and rewritten text goes into small "update" chunks (data/c/1000.gz and up), so the
  big chunks stay byte-for-byte the same and phones only download what actually changed.

--full rebuilds every chunk from scratch (much bigger download for everyone; use rarely,
e.g. after improving the HTML cleaner).

Run add_001.py, build_canons.py and build_links.py afterwards.
"""
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CDIR = DATA / "c"
UPDATE_BASE = 1000          # update chunks are numbered from here
UPDATE_TARGET = 1_400_000   # bytes of HTML per update chunk
PROPOSALS = 900             # chunk written by add_001.py


def gz_read(p):
    return json.loads(gzip.decompress(Path(p).read_bytes()))


def gz_write(p, obj):
    Path(p).write_bytes(gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), 9, mtime=0))


def digest(p):
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()[:10]


def build(src, out):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_data.py"), src, str(out)], check=True)
    return gz_read(Path(out) / "index.json.gz")


def full(src):
    with tempfile.TemporaryDirectory() as tmp:
        new = build(src, tmp)
        old = gz_read(DATA / "index.gz")
        for f in CDIR.glob("*.gz"):
            if f.stem != str(PROPOSALS):
                f.unlink()
        for f in (Path(tmp) / "c").glob("*.gz"):
            shutil.copy(f, CDIR / f.name)
        keep = [m for m in old["a"] if m.get("ch") == PROPOSALS]   # add_001.py refreshes these
        new["a"] = carry_proposals(new, old, keep)
        new["dv"] = time.strftime("%Y-%m-%d") + "-full"
        new["cv"] = {}
        new["updated"] = time.strftime("%Y-%m-%d")
        gz_write(DATA / "index.gz", new)
        print(f"Full rebuild: {len(new['a'])} articles")


def carry_proposals(new, old, keep):
    """Keep the 001 proposals (from add_001.py) in the list, with tag numbers remapped."""
    if not keep:
        return new["a"]
    ix = {t: i for i, t in enumerate(new["tags"])}
    for m in keep:
        g = []
        for i in m["g"]:
            t = old["tags"][i]
            if t not in ix:
                ix[t] = len(new["tags"]); new["tags"].append(t); new["tagCount"].append(0)
            g.append(ix[t])
        m["g"] = g
    ids = {m["id"] for m in keep}
    rest = [m for m in new["a"] if m["id"] not in ids]
    at = next((i for i, m in enumerate(rest) if m["k"] != "scp"), len(rest))
    return rest[:at] + keep + rest[at:]


def weekly(src):
    old = gz_read(DATA / "index.gz")
    old_by = {m["id"]: m for m in old["a"]}
    cv = dict(old.get("cv", {}))
    with tempfile.TemporaryDirectory() as tmp:
        new = build(src, tmp)
        chunk_cache = {}

        def text_of(m):
            n = m["ch"]
            if n not in chunk_cache:
                chunk_cache.clear()
                chunk_cache[n] = gz_read(Path(tmp) / "c" / f"{n:03d}.gz")
            return chunk_cache[n][m["id"]]

        fresh, added, rewritten = [], 0, 0
        for m in new["a"]:
            o = old_by.get(m["id"])
            if o and o.get("ch") == PROPOSALS:
                continue                                    # add_001.py looks after these
            if o:
                changed = abs(m["w"] - o["w"]) >= 150 and abs(m["w"] - o["w"]) > 0.15 * max(o["w"], 1)
                if not changed:
                    m["ch"], m["w"] = o["ch"], o["w"]      # keep the text phones already have
                    continue
                rewritten += 1
            else:
                added += 1
            fresh.append((m, text_of(m)))

        # fill the newest update chunk first, then start new ones
        upd = sorted(int(f.stem) for f in CDIR.glob("*.gz") if f.stem.isdigit() and int(f.stem) >= UPDATE_BASE)
        n = upd[-1] if upd else UPDATE_BASE
        cur = gz_read(CDIR / f"{n}.gz") if upd else {}
        size = sum(len(v) for v in cur.values())
        touched = set()
        for m, html in fresh:
            if size + len(html) > UPDATE_TARGET and cur:
                gz_write(CDIR / f"{n}.gz", cur); touched.add(n)
                n, cur, size = n + 1, {}, 0
            cur[m["id"]] = html; size += len(html)
            m["ch"] = n
        if cur:
            gz_write(CDIR / f"{n}.gz", cur); touched.add(n)
        for t in touched:
            cv[str(t)] = digest(CDIR / f"{t}.gz")

    keep = [m for m in old["a"] if m.get("ch") == PROPOSALS]
    new["a"] = carry_proposals(new, old, keep)
    new["dv"] = old.get("dv", old.get("built"))
    new["built"] = old.get("built", new["built"])
    new["updated"] = time.strftime("%Y-%m-%d")
    new["cv"] = cv
    removed = len(set(old_by) - {m["id"] for m in new["a"]})
    gz_write(DATA / "index.gz", new)
    print(f"Weekly update: {added} new, {rewritten} rewritten, {removed} removed, "
          f"{len(new['a'])} articles in total; update chunks touched: {sorted(touched)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    (full if "--full" in sys.argv else weekly)(sys.argv[1])
