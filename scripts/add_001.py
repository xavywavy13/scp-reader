"""Fetch every SCP-001 proposal from the SCP Wiki and add them to the app's archive.

The archive the app was built from leaves most 001 proposals out (they aren't
tagged as normal SCPs), so this script reads the wiki's "001-proposal" tag list,
downloads each proposal page, cleans it, and writes:

  data/c/900.gz   one chunk holding every proposal's article text
  data/index.gz   the article list, updated so the proposals appear under
                  "001 Proposals" in the app

It is safe to run again: it replaces its own chunk and entries each time.
Runs on GitHub Actions (see .github/workflows/add-001.yml).
"""
import gzip
import json
import re
import sys
import time
from pathlib import Path

import lxml.html
import requests

sys.path.insert(0, str(Path(__file__).parent))
from scp_clean import clean  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "index.gz"
CHUNK_NO = 900
CHUNK = ROOT / "data" / "c" / f"{CHUNK_NO:03d}.gz"
WIKI = "https://scp-wiki.wikidot.com"
TAG_PAGE = f"{WIKI}/system:page-tags/tag/001-proposal"
HUB_PAGE = f"{WIKI}/scp-001"
SERIES = "scp-001"
CLASS_TAGS = ["safe", "euclid", "keter", "thaumiel", "apollyon", "archon", "neutralized",
              "explained", "decommissioned", "pending", "esoteric-class", "ticonderoga",
              "cernunnos", "hiemal", "tiamat", "uncontained"]

session = requests.Session()
session.headers["User-Agent"] = "SCP-Field-Reader/1.0 (personal reader; github.com/xavywavy13/scp-reader)"


def get(url, tries=3):
    for i in range(tries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  {r.status_code} for {url}")
        except requests.RequestException as e:
            print(f"  error {e} for {url}")
        time.sleep(3 * (i + 1))
    return None


def proposal_slugs():
    """Slugs from the 001-proposal tag list, plus anything linked from the SCP-001 hub
    that is itself a proposal (checked later by its tags). Also returns the hub's label
    for each proposal, since most proposal pages are simply titled "SCP-001"."""
    slugs, labels = [], {}
    html = get(TAG_PAGE)
    if html:
        doc = lxml.html.fromstring(html)
        box = doc.xpath('//*[@id="tagged-pages-list"]') or doc.xpath('//*[contains(@class,"pages-list")]')
        links = box[0].xpath(".//a/@href") if box else []
        for h in links:
            if h.startswith("/") and not h.startswith("/system:"):
                slugs.append(h.strip("/").lower())
    print(f"tag list: {len(slugs)} pages")
    hub = get(HUB_PAGE)
    if hub:
        doc = lxml.html.fromstring(hub)
        pc = doc.get_element_by_id("page-content", None)
        if pc is not None:
            for a in pc.xpath(".//a[@href]"):
                h = a.get("href")
                if h.startswith("/") and ":" not in h and h.count("/") == 1:
                    s = h.strip("/").lower()
                    if not s or s == "scp-001":
                        continue
                    if s not in slugs:
                        slugs.append(s)
                    holder = a.getparent()
                    while holder is not None and holder.tag not in ("li", "p", "td", "div"):
                        holder = holder.getparent()
                    text = re.sub(r"\s+", " ", (holder if holder is not None else a).text_content()).strip()
                    if a.text_content().strip() and len(text) < 160:
                        labels.setdefault(s, text)
    return list(dict.fromkeys(slugs)), labels


def page_tags(doc):
    return [a.text_content().strip().lower() for a in doc.xpath('//div[contains(@class,"page-tags")]//a')
            if a.text_content().strip()]


def page_rating(doc):
    for el in doc.xpath('//*[contains(@class,"rate-points")]//*[contains(@class,"number")]'):
        m = re.search(r"[-+]?\d+", el.text_content())
        if m:
            return int(m.group())
    return 0


def page_title(doc):
    t = doc.xpath('//*[@id="page-title"]')
    return re.sub(r"\s+", " ", t[0].text_content()).strip() if t else ""


def split_title(title):
    """'CODE NAME: Jonathan Ball - Sheaf of Papers' -> ('CODE NAME: Jonathan Ball', 'Sheaf of Papers')."""
    m = re.match(r"^(.*?)\s+[-–—]\s+(.+)$", title)
    return (m.group(1).strip(), m.group(2).strip()) if m else (title, "")


def author_from(title, slug):
    m = re.match(r"(?:CODE NAME|Codename)\s*:\s*(.+)", title, re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r"(.+?)['’]s proposal", slug.replace("-s-proposal", "'s proposal").replace("-", " "), re.I)
    return m.group(1).strip().title() if m else ""


def main():
    index = json.loads(gzip.decompress(INDEX.read_bytes()))
    tags = index["tags"]
    tag_ix = {t: i for i, t in enumerate(tags)}
    tag_count = index.get("tagCount", [0] * len(tags))

    slugs, labels = proposal_slugs()
    if not slugs:
        sys.exit("No proposals found; the wiki may be unreachable. Nothing changed.")

    chunk, entries = {}, []
    for n, slug in enumerate(slugs, 1):
        html = get(f"{WIKI}/{slug}")
        time.sleep(1.0)  # be gentle with the wiki
        if not html:
            continue
        doc = lxml.html.fromstring(html)
        tg = page_tags(doc)
        if "001-proposal" not in tg:
            continue
        body = clean(html)
        if len(body) < 40:
            continue
        title = page_title(doc) or slug
        if labels.get(slug) and (title.upper().startswith("SCP-001") or len(labels[slug]) > len(title)):
            title = labels[slug]
        if title.upper() == "SCP-001":
            m = re.match(r"(.+?)-s-proposal", slug)
            title = (m.group(1).replace("-", " ").title() + "'s Proposal") if m else slug.replace("-", " ").title()
        name, nick = split_title(title)
        g = []
        for t in tg:
            if t.startswith("_"):
                continue
            if t not in tag_ix:
                tag_ix[t] = len(tags)
                tags.append(t)
                tag_count.append(0)
            tag_count[tag_ix[t]] += 1
            g.append(tag_ix[t])
        entries.append({
            "id": slug, "k": "scp", "t": name, "n": nick,
            "r": page_rating(doc), "a": author_from(name, slug), "d": "",
            "g": g, "w": len(re.sub(r"<[^>]+>", " ", body).split()),
            "s": SERIES, "c": next((c for c in CLASS_TAGS if c in tg), "thaumiel"),
            "ch": CHUNK_NO,
        })
        chunk[slug] = body
        print(f"[{n}/{len(slugs)}] {slug}: {title}")

    if not entries:
        sys.exit("No proposal pages could be read. Nothing changed.")

    new_ids = {e["id"] for e in entries}
    # drop earlier copies (this script's previous run, or the few proposals filed as tales)
    kept = [m for m in index["a"] if m["id"] not in new_ids and not (m.get("ch") == CHUNK_NO)]
    # put the proposals right after SCP-001 itself, highest rated first
    entries.sort(key=lambda e: -e["r"])
    at = next((i + 1 for i, m in enumerate(kept) if m["id"] == "scp-001"), None)
    if at is None:  # no SCP-001 page in the archive: place them at the end of the SCP items
        at = next((i for i, m in enumerate(kept) if m["k"] != "scp"), len(kept))
    index["a"] = kept[:at] + entries + kept[at:]
    index["tags"], index["tagCount"] = tags, tag_count
    for h in index.get("hubs", []):
        h["m"] = [i for i in h["m"] if i in {m["id"] for m in index["a"]}]
    index["proposalsUpdated"] = time.strftime("%Y-%m-%d")

    CHUNK.write_bytes(gzip.compress(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode(), 9))
    INDEX.write_bytes(gzip.compress(json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode(), 9))
    print(f"Added {len(entries)} proposals ({CHUNK.stat().st_size // 1024} KB).")


if __name__ == "__main__":
    main()
