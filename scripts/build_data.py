"""Build a compact, cleaned SCP reader dataset from the scp-data dump.

Usage: python scripts/build_data.py <path to scp-api/docs/data/scp> <output data dir>
Get the dump with: git clone https://github.com/scp-data/scp-api (about 1 GB).
After building, run add_001.py (or the GitHub Action) to add the SCP-001 proposals.

Output (in OUT):
  index.json.gz         compact metadata for every article
  c/NNN.gz              gzipped JSON chunks {id: cleaned_html}
"""
import ast, glob, gzip, json, os, re, sys
from collections import Counter
import lxml.html
from lxml import etree

SRC = sys.argv[1]
OUT = sys.argv[2]
os.makedirs(os.path.join(OUT, "c"), exist_ok=True)

def lit(v, default):
    if isinstance(v, (list, dict)):
        return v
    try:
        return ast.literal_eval(v) if v else default
    except Exception:
        return default

# ---------- nicknames from series hub pages ----------
hubs = json.load(open(f"{SRC}/hubs/index.json"))
nick = {}
NICK_RE = re.compile(r'<li><a href="/([^"]+)">([^<]+)</a>\s*-\s*(.*?)</li>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
for k, h in hubs.items():
    html = h.get("raw_content") or ""
    for slug, _label, rest in NICK_RE.findall(html):
        name = TAG_RE.sub("", rest).strip()
        name = lxml.html.fromstring(f"<span>{name}</span>").text_content().strip() if name else ""
        if name and slug not in nick:
            nick[slug.lower()] = name[:140]

# ---------- HTML cleaning: shared with add_001.py ----------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scp_clean import clean  # noqa: E402

# ---------- collect ----------
CLASS_TAGS = ["safe", "euclid", "keter", "thaumiel", "apollyon", "archon", "neutralized",
              "explained", "decommissioned", "pending", "esoteric-class", "ticonderoga", "cernunnos",
              "hiemal", "tiamat", "uncontained"]
CLASS_RE = re.compile(r"Object Class:?\s*</strong>\s*([A-Za-z\-]+)", re.I)

records = []  # (sortkey, meta, html)
def add(kind, v, extra):
    link = (v.get("link") or "").lower()
    if not link:
        return
    raw = v.get("raw_content") or ""
    html = clean(raw, v.get("raw_source"))
    if len(html) < 40:
        return
    tags = [t for t in lit(v.get("tags"), []) if not t.startswith("_")]
    m = {
        "id": link,
        "k": kind,
        "t": extra.get("title") or v.get("title") or link,
        "r": int(v.get("rating") or 0) if str(v.get("rating") or "0").lstrip("-").isdigit() else 0,
        "a": v.get("creator") or v.get("created_by") or "",
        "d": (v.get("created_at") or "")[:10],
        "g": tags,
        "w": len(re.sub(r"<[^>]+>", " ", html).split()),
    }
    m.update({k: x for k, x in extra.items() if k != "title" and x not in (None, "")})
    records.append((extra.get("_sort"), m, html))

def series_order(s):
    mm = re.match(r"series-(\d+)", s or "")
    return int(mm.group(1)) if mm else 100 + hash(s) % 10

for f in sorted(glob.glob(f"{SRC}/items/content_*.json")):
    if f.endswith("content_index.json"):
        continue
    d = json.load(open(f))
    for v in d.values():
        tags = lit(v.get("tags"), [])
        cls = next((c for c in CLASS_TAGS if c in tags), "")
        if not cls:
            mm = CLASS_RE.search(v.get("raw_content") or "")
            cls = mm.group(1).lower() if mm else ""
        num = v.get("scp_number")
        try: num = int(num)
        except Exception: num = None
        link = (v.get("link") or "").lower()
        add("scp", v, {
            "title": v.get("scp") or v.get("title"),
            "n": nick.get(link, ""),
            "s": v.get("series") or "",
            "c": cls,
            "no": num,
            "_sort": (0, series_order(v.get("series")), num if num is not None else 10**9, link),
        })
    print("items", f, len(records), flush=True)

def tale_title(t):
    return re.sub(r"\s*-\s*SCP Foundation\s*$", "", t or "").strip()

for f in sorted(glob.glob(f"{SRC}/tales/content_*.json")):
    if f.endswith("content_index.json"):
        continue
    d = json.load(open(f))
    for v in d.values():
        add("tale", v, {"title": tale_title(v.get("title")), "_sort": (1, 0, (v.get("created_at") or ""), v.get("link") or "")})
    print("tales", f, len(records), flush=True)

d = json.load(open(f"{SRC}/goi/content_goi.json"))
for v in d.values():
    add("goi", v, {"title": tale_title(v.get("title")), "_sort": (2, 0, (v.get("created_at") or ""), v.get("link") or "")})
print("goi", len(records), flush=True)

# de-dup ids
seen = set(); uniq = []
for r in sorted(records, key=lambda r: r[0]):
    if r[1]["id"] in seen: continue
    seen.add(r[1]["id"]); uniq.append(r)

# ---------- chunk ----------
TARGET = 1_400_000
chunk, size, ci = {}, 0, 0
total_gz = 0
def flush():
    global chunk, size, ci, total_gz
    if not chunk: return
    data = gzip.compress(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    open(f"{OUT}/c/{ci:03d}.gz", "wb").write(data)
    total_gz += len(data)
    chunk, size, ci = {}, 0, ci + 1
metas = []
for _, m, html in uniq:
    if size + len(html) > TARGET and chunk:
        flush()
    chunk[m["id"]] = html; size += len(html)
    m["ch"] = ci
    metas.append(m)
flush()

# ---------- tags & hubs ----------
tagc = Counter(t for m in metas for t in m["g"])
taglist = [t for t, _ in tagc.most_common()]
tix = {t: i for i, t in enumerate(taglist)}
for m in metas:
    m["g"] = [tix[t] for t in m["g"]]
ids = {m["id"] for m in metas}
hublist = []
for k, h in hubs.items():
    refs = [r.lower() for r in lit(h.get("references"), []) if r.lower() in ids]
    refs = list(dict.fromkeys(refs))
    if len(refs) >= 4:
        hublist.append({"id": k, "t": tale_title(h.get("title")) or k, "m": refs})
hublist.sort(key=lambda h: -len(h["m"]))

index = {"v": 1, "built": "", "tags": taglist, "tagCount": [tagc[t] for t in taglist], "hubs": hublist, "a": metas}
import datetime
index["built"] = datetime.date.today().isoformat()
raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode()
open(f"{OUT}/index.json.gz", "wb").write(gzip.compress(raw, 9))
print("articles", len(metas), "chunks", ci, "chunk gz MB", round(total_gz / 1e6, 1),
      "index raw MB", round(len(raw) / 1e6, 2), "nicknames", sum(1 for m in metas if m.get("n")))
