"""Build data/canons.gz: every canon on the SCP Wiki's Canon Hub, and every story series in its
Series Archive, with their articles in order. Story series are marked "type": "series".

For each canon listed on https://scp-wiki.wikidot.com/canon-hub this reads the canon's own
hub page and records:

  - name, the Canon Hub blurb and its featured quote
  - the canon's tag (from the hub page's tags; articles in the canon carry it)
  - sections in the order the hub presents them (headings, tabs and collapsibles become
    sections), each listing the articles it links to

Which articles count as part of the canon: when the canon has a tag, an article must carry
it. Tagged articles the hub doesn't link to are added at the end, oldest first, under
"More from this canon". Canons without a tag use every archive article their hub links to.

Runs on GitHub Actions (see .github/workflows/build-canons.yml). Needs data/index.gz.
For a local test against the scp-data dump:  python scripts/build_canons.py --dump <scp-api/docs/data/scp>
"""
import gzip
import json
import re
import sys
import time
from pathlib import Path

import lxml.html

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "index.gz"
OUT = ROOT / "data" / "canons.gz"
WIKI = "https://scp-wiki.wikidot.com"
GENERIC_TAGS = {"hub", "canon-hub", "group-hub", "series-hub", "co-authored", "collaboration",
                "illustrated", "audio", "video", "rewrite", "featured", "reviewers-spotlight",
                "artwork", "contest", "event", "metadata", "tale", "scp", "goi-format"}
WS = re.compile(r"\s+")


# ---------------------------------------------------------------- fetching
class WikiSource:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "SCP-Field-Reader/1.0 (personal reader; github.com/xavywavy13/scp-reader)"

    def get(self, url, tries=3):
        for i in range(tries):
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 200:
                    return r.text
                print(f"  {r.status_code} for {url}")
                if r.status_code == 404:
                    return None
            except Exception as e:  # noqa: BLE001
                print(f"  error {e} for {url}")
            time.sleep(3 * (i + 1))
        return None

    def page(self, slug):
        html = self.get(f"{WIKI}/{slug}")
        time.sleep(1.0)  # be gentle with the wiki
        if not html:
            return None
        doc = lxml.html.fromstring(html)
        tags = [a.text_content().strip().lower() for a in doc.xpath('//div[contains(@class,"page-tags")]//a')]
        pc = doc.get_element_by_id("page-content", None)
        if pc is None:
            return None
        # app-style hubs keep their content in [[html]] blocks: fetch those frames too
        for fr in pc.xpath('.//iframe[contains(@class,"html-block-iframe")]'):
            src = fr.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            inner = self.get(src) if src.startswith("http") else None
            if inner:
                try:
                    frag = lxml.html.fromstring(inner)
                    body = frag.find(".//body")
                    box = lxml.html.Element("div")
                    for ch in list(body if body is not None else frag):
                        box.append(ch)
                    fr.getparent().replace(fr, box)
                except Exception:  # noqa: BLE001
                    pass
        return pc, tags


class DumpSource:
    """Reads pages from the scp-data dump instead of the wiki (for local testing)."""
    def __init__(self, path):
        self.hubs = json.load(open(Path(path) / "hubs" / "index.json"))

    def page(self, slug):
        v = self.hubs.get(slug)
        if not v:
            return None
        doc = lxml.html.fromstring(v["raw_content"])
        pc = doc.get_element_by_id("page-content", None)
        if pc is None:
            pc = doc
        source = v.get("raw_source") or ""
        blocks = re.findall(r"\[\[html[^\]]*\]\](.*?)\[\[/html\]\]", source, re.S | re.I)
        for fr, b in zip(pc.xpath('.//iframe[contains(@class,"html-block-iframe")]'), blocks):
            try:
                frag = lxml.html.fromstring(f"<div>{b}</div>")
                fr.getparent().replace(fr, frag)
            except Exception:  # noqa: BLE001
                pass
        tg = v.get("tags") or []
        if isinstance(tg, str):
            import ast
            tg = ast.literal_eval(tg)
        return pc, [t.lower() for t in tg]


# ---------------------------------------------------------------- parsing
def text(el):
    return WS.sub(" ", el.text_content()).strip()


def slug_of(href):
    if not href:
        return None
    m = re.match(r"^(?:https?://(?:www\.)?(?:scp-wiki\.wikidot\.com|scpwiki\.com|scp-wiki\.net))?/([^/#?:]+)/?(?:[#?].*)?$", href.strip())
    return m.group(1).lower() if m else None


def canon_list(src):
    got = src.page("canon-hub")
    if not got:
        sys.exit("Couldn't read the Canon Hub. Nothing changed.")
    pc, _ = got
    out, unmaintained = [], False
    for el in pc.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag in ("h1", "h2") and not el.xpath('ancestor::div[contains(@class,"canon-block")]'):
            if "unmaintained" in text(el).lower():
                unmaintained = True
            continue
        cls = el.get("class") or ""
        if "canon-block" not in cls.split() or "opener" in cls.split():
            continue
        h = el.xpath(".//h2")
        link = el.xpath(".//h2//a[@href]")
        if not h or not link:
            continue
        slug = slug_of(link[0].get("href"))
        if not slug:
            continue
        paras = [text(p) for p in el.xpath("./p")]
        snip = el.xpath('.//div[contains(@class,"snippet")]')
        quote, qsrc = "", None
        if snip:
            quote = " ".join(text(p) for p in snip[0].xpath("./p"))
            sa = snip[0].xpath('.//div[contains(@class,"source")]/a[@href]')
            if sa:
                qsrc = {"id": slug_of(sa[0].get("href")), "t": text(sa[0])}
            by = snip[0].xpath('.//div[contains(@class,"source")]//span[contains(@class,"printuser")]')
            if by and qsrc:
                qsrc["a"] = text(by[-1])
        img = next((("https:" + i if i.startswith("//") else i) for i in el.xpath(".//img/@src")), None)
        out.append({"id": slug, "t": text(h[0]), "d": clean_desc(" ".join(p for p in paras if p)), "img": img,
                    "q": quote[:600], "qs": qsrc, "un": unmaintained})
    return out


CREDIT = re.compile(r"(?i)\b(filename|file name|license|licen[cs]ed under|source link|derivative of|image credit|cc by-sa|cc-by-sa|public domain)\b")
SKIP_INTRO = re.compile(r"(?i)^(welcome to|this (page|hub) (is|contains|lists)|note:|warning:|content warning|cw:|last updated|see also|return to|back to)")


def clean_desc(t):
    """Drop image-credit text that some hubs put where the blurb goes."""
    t = WS.sub(" ", t or "").strip()
    m = CREDIT.search(t)
    if m:
        t = t[:m.start()].strip(" -–—:|")
    if re.search(r"(?i)a blurb should go here", t) or len(t) < 25:
        return ""
    return t[:500]


def hub_intro(pc, name):
    """The hub's own introduction: its first real paragraphs of prose."""
    out = []
    for p in pc.xpath(".//p"):
        if p.xpath('ancestor::*[contains(@class,"licensebox") or contains(@class,"footnotes") or contains(@class,"collapsible-block") '
                   'or contains(@class,"footer") or contains(@class,"credit") or contains(@class,"yui-nav") or self::table or self::blockquote]'):
            continue
        t = WS.sub(" ", p.text_content()).strip()
        if len(t) < 60 or CREDIT.search(t) or SKIP_INTRO.search(t) or t.lower().startswith(name.lower()):
            continue
        links = len(p.xpath(".//a"))
        if links > 3 and links * 25 > len(t) / 2:       # a list of links, not prose
            continue
        out.append(t)
        if sum(len(x) for x in out) > 220:
            break
    text_ = " ".join(out)
    return (text_[:420].rsplit(" ", 1)[0] + "…") if len(text_) > 420 else text_


def hub_image(pc):
    for img in pc.xpath(".//img[@src]"):
        if img.xpath('ancestor::*[contains(@class,"licensebox") or contains(@class,"footer") or contains(@class,"printuser")]'):
            continue
        src = img.get("src")
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http") and not re.search(r"(?i)(avatar|karma|userinfo|icon|favicon|\.gif$)", src):
            return src
    return None


def clean_series_name(t):
    t = re.sub(r"(?i)\s*\((series )?hub\)\s*$", "", t)
    t = re.sub(r"(?i)\s+(series\s+)?hub\s*$", "", t).strip()
    return t


def series_list(src):
    """Story series from the wiki's Series Archive: the blocks for series with their own tag,
    and the paged list of series hubs."""
    out, seen, pages = [], set(), ["series-archive"]
    first = src.page("series-archive")
    if not first:
        print("Couldn't read the Series Archive; story series skipped")
        return out
    pc, _ = first
    nums = [int(x) for x in re.findall(r"/series-archive/p/(\d+)", lxml.html.tostring(pc, encoding="unicode"))]
    pages += [f"series-archive/p/{i}" for i in range(2, max(nums, default=1) + 1)]
    for i, pg in enumerate(pages):
        got = first if i == 0 else src.page(pg)
        if not got:
            continue
        for blk in got[0].xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," series-block ")]'):
            cls = (blk.get("class") or "").split()
            h = blk.xpath("./h3") or blk.xpath('./div[@class="title"]')
            if not h:
                continue
            name = clean_series_name(text(h[0]))
            link = h[0].xpath(".//a[@href]")
            slug = slug_of(link[0].get("href")) if link else None
            tag_el = blk.xpath('.//div[contains(@class,"tag-lead")]//a')
            tag = text(tag_el[0]).lower() if tag_el else None
            blurb = " ".join(t for t in (text(p) for p in blk.xpath('./div[contains(@class,"blurb")]//p|./div[contains(@class,"description")]')) if t)
            if re.search(r"(?i)a blurb should go here", blurb):
                blurb = ""
            key = slug or ("tag:" + (tag or name.lower()))
            if not name or key in seen:
                continue
            seen.add(key)
            img = next((("https:" + i if i.startswith("//") else i) for i in blk.xpath(".//img/@src")), None)
            out.append({"id": slug or key, "t": name, "d": clean_desc(blurb), "img": img, "q": "", "qs": None, "un": False,
                        "type": "series", "done": "_complete" in cls, "hub": bool(slug), "tag0": tag})
    return out


def tab_titles(navset):
    return [text(li) for li in navset.xpath('./ul[contains(@class,"yui-nav")]/li')]


SKIP_SECTION = re.compile(r"(?i)\b(latest|newest|most recent|recent(ly)?|random|featured|spotlight|new (entries|articles|additions))\b")


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def hub_sections(pc, accept, canon_name):
    """Walk the hub in reading order. Headings, tab titles and collapsible labels start a
    new section; every accepted article link is placed in the current section. Sections
    such as "Latest entries" are skipped so they don't pull articles out of reading order."""
    sections, seen = [], set()
    cur = {"t": "", "m": []}
    sections.append(cur)
    cname = norm(canon_name)

    def start(title):
        nonlocal cur
        title = WS.sub(" ", title or "").strip(" :–—-")
        # "+ Show entries" style labels: keep only a real name, if there is one
        title = re.sub(r"^[+»>\s]*", "", title)
        title = re.sub(r"(?i)^(show|hide|open|close|expand|view|reveal|click to (show|open|expand))\b\s*", "", title)
        title = re.sub(r"(?i)\s*\b(entries|block|list|more)\b\s*$", "", title).strip()
        if title.lower() in ("", "all", "here", "contents"):
            return
        if SKIP_SECTION.search(title):
            cur = {"t": title, "m": [], "skip": True}
            sections.append(cur)
            return
        if cname and norm(title).replace(" hub", "") == cname:
            return
        title = re.sub(r"(?i)\bclick here\b!?", "Start here", title).replace("? Start here", ": start here")
        if not title or title.lower() == canon_name.lower() or len(title) > 80:
            return
        if cur["t"] == title:
            return
        cur = {"t": title, "m": []}
        sections.append(cur)

    pane_title = {}
    for ns in pc.xpath('.//div[contains(@class,"yui-navset")]'):
        titles = tab_titles(ns)
        for i, pane in enumerate(ns.xpath('./div[contains(@class,"yui-content")]/div')):
            if i < len(titles):
                pane_title[pane] = titles[i]

    for el in pc.iter():
        if not isinstance(el.tag, str):
            continue
        cls = (el.get("class") or "").split()
        if el in pane_title:
            start(pane_title[el])
        elif el.tag in ("h1", "h2", "h3", "h4") and not el.xpath("ancestor::a"):
            start(text(el))
        elif "collapsible-block-folded" in cls:
            # "+ Show entries" style labels: keep only a real name, if there is one
            label = re.sub(r"^[+»>\s]*", "", text(el))
            label = re.sub(r"(?i)^(show|open|expand|view|reveal|click to (show|open|expand))\b\s*", "", label)
            label = re.sub(r"(?i)\s*\b(entries|block|list|more)\b\s*$", "", label).strip()
            if label and label.lower() not in ("all", "here", "contents"):
                start(label)
        elif el.tag == "a":
            if el.xpath('ancestor::*[contains(@class,"breadcrumbs") or contains(@class,"footer-wikiwalk-nav") or contains(@class,"page-tags") or contains(@class,"licensebox") or contains(@class,"yui-nav")]'):
                continue
            if el.xpath('ancestor::*[contains(@class,"collapsible-block-unfolded-link")]'):
                continue
            s = slug_of(el.get("href"))
            if cur.get("skip"):
                continue
            if s and s not in seen and accept(s):
                seen.add(s)
                cur["m"].append(s)
    return [s for s in sections if s["m"] and not s.get("skip")]


def tidy_sections(sections, arts):
    """Many hubs give every article its own heading. A one-article section whose title is
    really that article's name is folded back into the section before it."""
    out = []
    for sec in sections:
        if len(sec["m"]) == 1 and sec["t"]:
            m = arts[sec["m"][0]]
            names = [norm(x) for x in (m.get("t"), m.get("n"), m["id"].replace("-", " ")) if x]
            t = norm(sec["t"])
            if any(n and (n in t or t in n) for n in names):
                sec = {"t": "", "m": sec["m"]}
        if out and not sec["t"]:
            out[-1]["m"].extend(sec["m"])
        else:
            out.append({"t": sec["t"], "m": list(sec["m"])})
    return out


def pick_tag(hub_tags, tag_count, canon):
    """The canon's own tag: a non-generic hub tag that many articles carry."""
    cands = [t for t in hub_tags if t not in GENERIC_TAGS and not t.startswith("_") and tag_count.get(t, 0) >= 3]
    if not cands:
        return None
    words = set(re.findall(r"[a-z0-9]+", (canon["t"] + " " + canon["id"]).lower()))
    def score(t):
        overlap = len(set(t.split("-")) & words)
        return (overlap, tag_count.get(t, 0))
    best = max(cands, key=score)
    return best if score(best)[0] > 0 or len(cands) == 1 else None


def guess_tag(canon, tag_count):
    """When the hub page gives no usable tag, try the tag the canon's name would have."""
    def slugify(t):
        return re.sub(r"[^a-z0-9]+", "-", t.lower().replace("&", "and")).strip("-")
    base = re.sub(r"-(hub|homescreen|home)$", "", canon["id"])
    for t in (slugify(canon["t"]), slugify(re.sub(r"(?i)^the\s+", "", canon["t"])), base, base.replace("-hub", "")):
        if t and tag_count.get(t, 0) >= 3:
            return t
    return None


# ---------------------------------------------------------------- main
def main():
    src = DumpSource(sys.argv[sys.argv.index("--dump") + 1]) if "--dump" in sys.argv else WikiSource()
    index = json.loads(gzip.decompress(INDEX.read_bytes()))
    arts = {m["id"]: m for m in index["a"]}
    tags = index["tags"]
    tag_count = dict(zip(tags, index.get("tagCount", [])))
    by_tag = {}
    for m in index["a"]:
        for g in m["g"]:
            by_tag.setdefault(tags[g], []).append(m["id"])

    canons = canon_list(src)
    print(f"Canon Hub lists {len(canons)} canons")
    series = series_list(src)
    print(f"Series Archive lists {len(series)} story series")
    canon_ids = {c["id"] for c in canons}
    series = [x for x in series if x["id"] not in canon_ids]
    everything = canons + series
    out = []
    for n, c in enumerate(everything, 1):
        is_series = c.get("type") == "series"
        got = src.page(c["id"]) if (not is_series or c.get("hub")) else None
        hub_tags = got[1] if got else []
        tag0 = c.pop("tag0", None) if is_series else None
        c.pop("hub", None)
        if tag0 and tag_count.get(tag0, 0) < 2:
            tag0 = None
        # story series only use the tag the Series Archive names (or one spelled like the
        # series); a hub's other tags are often broad ones like "horror" or "international"
        tag = tag0 or guess_tag(c, tag_count) if is_series else (pick_tag(hub_tags, tag_count, c) or guess_tag(c, tag_count))
        if is_series and tag and tag_count.get(tag, 0) > 400:
            tag = None
        members = set(by_tag.get(tag, [])) if tag else None

        def accept(s):
            return s in arts and (members is None or s in members) and s != c["id"]

        sections = tidy_sections(hub_sections(got[0], accept, c["t"]), arts) if got else []
        listed = {s for sec in sections for s in sec["m"]}
        if members:
            rest = sorted((s for s in members if s not in listed and s in arts),
                          key=lambda s: (arts[s].get("d") or "9999", s))
            if rest:
                sections.append({"t": ("More from this series" if is_series else "More from this canon") if listed else "", "m": rest})
        total = sum(len(s["m"]) for s in sections)
        if total < 2:
            print(f"[{n}/{len(everything)}] {c['t']}: skipped ({total} articles found; hub page "
                  f"{'read' if got else 'not read'}, {len(hub_tags)} tags, tag {tag})")
            continue
        if sections and not sections[0]["t"] and len(sections) > 1:
            sections[0]["t"] = "Start here"
        if c["qs"] and c["qs"].get("id") not in arts:
            c["qs"]["id"] = None
        if got:
            if not c.get("d"):
                c["d"] = hub_intro(got[0], c["t"])
            if not c.get("img"):
                c["img"] = hub_image(got[0])
        if not c.get("img"):
            c.pop("img", None)
        out.append({**c, "tag": tag, "s": sections, "n": total})
        print(f"[{n}/{len(everything)}] {c['t']}: {total} articles in {len(sections)} sections (tag {tag})")

    data = {"v": 1, "built": time.strftime("%Y-%m-%d"), "c": out}
    OUT.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), 9, mtime=0))
    print(f"Wrote {sum(1 for c in out if c.get('type') != 'series')} canons and "
          f"{sum(1 for c in out if c.get('type') == 'series')} story series to {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
