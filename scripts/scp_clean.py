"""HTML cleaner shared by the SCP Field Reader data scripts.

clean(raw_html) takes a wiki page (or just its #page-content) and returns the
article body with ratings, licence boxes and scripts removed, collapsibles
turned into <details>, tab views into simple tab sets, ACS bars into a compact
grid, footnotes and internal links tagged for the app, and inline styles reduced
to a safe set.
"""
import re
import lxml.html
from lxml import etree

# ---------- HTML cleaning ----------
DROP_CLASSES = {
    "page-rate-widget-box", "creditRate", "modalcontainer", "rateBox", "licensebox",
    "credit-module", "rate-box-with-credit-button", "authorbox", "footer-wikiwalk-nav",
    "page-tags", "creditButton", "fader", "close-credits", "code-block-hidden",
}
KEEP_STYLE = {"text-align", "font-weight", "font-style", "text-decoration", "text-transform",
              "font-variant", "letter-spacing"}
KEEP_CLASS = {"footnoteref", "footnotes-footer", "footnote-footer", "blockquote",
              "wiki-content-table", "code", "block-right", "block-left", "block-center",
              "scp-image-block", "scp-image-caption"}
WIKI_HOSTS = ("scp-wiki.wikidot.com", "www.scp-wiki.net", "scp-wiki.net", "scpwiki.com", "www.scpwiki.com")
FN_RE = re.compile(r"scrollToReference\('([^']+)'\)")

def clean_style(s):
    out = []
    for part in s.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip().lower()
        if k in KEEP_STYLE and "{$" not in v:
            out.append(f"{k}:{v.strip()}")
    return ";".join(out)

def classes(el):
    return set((el.get("class") or "").split())

def text_of(el, sel):
    r = el.cssselect(sel) if hasattr(el, "cssselect") else []
    return r[0].text_content().strip() if r else ""

def acs_block(el):
    """Replace the ACS anomaly bar with a compact, semantic summary."""
    def q(cls):
        for d in el.iter():
            if cls in classes(d):
                t = d.text_content().strip()
                for c in d.iter():
                    if "class-text" in classes(c):
                        t = c.text_content().strip()
                return t
        return ""
    data = [("Clearance", q("level")), ("Containment", q("contain-class")),
            ("Secondary", q("second-class")), ("Disruption", q("disrupt-class")),
            ("Risk", q("risk-class"))]
    box = etree.Element("div", {"class": "acs"})
    for label, val in data:
        val = re.sub(r"\s+", " ", val)
        if not val or val.lower() in ("none", "") or "{$" in val:
            continue
        cell = etree.SubElement(box, "div", {"class": "acs-c"})
        a = etree.SubElement(cell, "span"); a.text = label
        b = etree.SubElement(cell, "b"); b.text = val
    return box if len(box) else None

def remove(el):
    p = el.getparent()
    if p is None:
        return
    if el.tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + el.tail
        else:
            p.text = (p.text or "") + el.tail
    p.remove(el)

HTML_BLOCK_RE = re.compile(r"\[\[html[^\]]*\]\](.*?)\[\[/html\]\]", re.S | re.I)
IFRAME_RE = re.compile(r"<iframe[^>]*html-block-iframe[^>]*>\s*</iframe>", re.I)
WIKI = "https://scp-wiki.wikidot.com"


def inline_html_blocks(html, source):
    """Some articles put their text inside an [[html]] block, which the wiki shows in an
    iframe. The crawl only kept the empty iframe, so put the block's HTML back in its place,
    taken from the page source (blocks and iframes appear in the same order)."""
    if not source or "html-block-iframe" not in html:
        return html
    blocks = HTML_BLOCK_RE.findall(source)
    if not blocks:
        return html
    it = iter(blocks)

    def sub(_m):
        b = next(it, None)
        if b is None:
            return ""
        b = re.sub(r"<!doctype[^>]*>|</?html[^>]*>|<head[^>]*>.*?</head>|</?body[^>]*>", "", b, flags=re.S | re.I)
        b = re.sub(r'<a href="([^"]*)">\1</a>', r"\1", b)  # undo the crawler's auto-linking of bare URLs
        return f'<div class="htmlblock">{b}</div>'
    return IFRAME_RE.sub(sub, html)


def looks_like_code(txt):
    """Hidden blocks that hold page CSS, theme code or template variables, not story text."""
    return ("{$" in txt or "@import" in txt or "var(--" in txt
            or txt.count("{") >= 3 or txt.count(";") > len(txt.split()) / 4)


def decrypt_classified(root, source):
    """SCP-3125 (and translations using its keypad component) ship the article body
    encrypted and decrypt it in the browser once you enter the code. Do that here so the
    file can be read."""
    els = root.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," classified-info ")]')
    m = re.search(r'"abcdefghijklmnopqrstuvwxyz"\s*,\s*\w+\s*=\s*"([a-z]+)"', source or "")
    if not els or not m:
        return
    key, abc = m.group(1), "abcdefghijklmnopqrstuvwxyz"
    from urllib.parse import unquote

    def dec(text):
        out = []
        for i, ch in enumerate(text):
            k = abc.index(key[i % len(key)])
            low = ch.lower()
            if low in abc:
                d = abc[(abc.index(low) + 26 - k) % 26]
                out.append(d.upper() if ch != low else d)
            else:
                out.append(ch)
        try:
            return unquote("".join(out), errors="strict")
        except Exception:
            return "".join(out)
    for el in els:
        for node in el.iter():
            if not isinstance(node.tag, str):
                continue
            if node.text:
                node.text = dec(node.text)
            if node is not el and node.tail:
                node.tail = dec(node.tail)
        st = el.get("style") or ""
        el.set("style", re.sub(r"display\s*:\s*none\s*;?", "", st, flags=re.I))


def clean(html, source=None):
    if not html:
        return ""
    html = inline_html_blocks(html, source)
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return ""
    pc = doc.get_element_by_id("page-content", None)
    root = pc if pc is not None else doc
    decrypt_classified(root, source)
    for bad in root.xpath(".//script|.//style|.//iframe|.//object|.//embed|.//form|.//noscript|.//link|.//meta|.//input|.//button|.//select|.//textarea|.//audio|.//video"):
        remove(bad)
    # ACS bars
    for el in root.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," anom-bar-container ")]'):
        rep = acs_block(el)
        if rep is not None:
            rep.tail = el.tail
            el.getparent().replace(el, rep)
        else:
            remove(el)
    # dropped components
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        if classes(el) & DROP_CLASSES and el.getparent() is not None:
            remove(el)
    # collapsibles -> <details>
    for el in root.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," collapsible-block ")]'):
        folded = el.xpath('.//*[contains(@class,"collapsible-block-folded")]//a')
        content = el.xpath('.//*[contains(@class,"collapsible-block-content")]')
        label = folded[0].text_content().strip() if folded else "Show"
        label = re.sub(r"^\s*\+\s*", "", label) or "Show"
        det = etree.Element("details")
        summ = etree.SubElement(det, "summary"); summ.text = label
        if content:
            c = content[0]
            det.text = None
            body = etree.SubElement(det, "div", {"class": "det-body"})
            body.text = c.text
            for ch in list(c):
                body.append(ch)
        det.tail = el.tail
        el.getparent().replace(el, det) if el.getparent() is not None else None
    # tab views -> tabset
    for el in root.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," yui-navset ")]'):
        titles = [a.text_content().strip() for a in el.xpath('./ul[contains(@class,"yui-nav")]/li')]
        panes = el.xpath('./div[contains(@class,"yui-content")]/div')
        ts = etree.Element("div", {"class": "tabset"})
        bar = etree.SubElement(ts, "div", {"class": "tabbar", "role": "tablist"})
        for i, t in enumerate(titles):
            b = etree.SubElement(bar, "span", {"class": "tab" + (" on" if i == 0 else ""), "data-i": str(i), "role": "tab", "tabindex": "0"})
            b.text = t or f"Tab {i+1}"
        for i, p in enumerate(panes):
            sec = etree.SubElement(ts, "section", {"class": "pane", "data-i": str(i)})
            if i:
                sec.set("hidden", "")
            sec.text = p.text
            for ch in list(p):
                sec.append(ch)
        ts.tail = el.tail
        if el.getparent() is not None:
            el.getparent().replace(el, ts)
    # elements hidden by inline style. Inside an embedded HTML block they are usually
    # content revealed by a script (which the app can't run), so show them. Elsewhere,
    # small ones are template leftovers or duplicates and go; real hidden text becomes
    # a tap-to-reveal section rather than being lost.
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None:
            continue
        in_block = bool(el.xpath('ancestor::div[@class="htmlblock"]'))
        if in_block and el.get("hidden") is not None:
            del el.attrib["hidden"]
        st = (el.get("style") or "").replace(" ", "").lower()
        if "display:none" not in st:
            continue
        txt = el.text_content()
        if in_block:
            el.set("style", re.sub(r"display\s*:\s*none\s*;?", "", el.get("style"), flags=re.I))
        elif len(txt.split()) >= 25 and not looks_like_code(txt):
            det = etree.Element("details")
            summ = etree.SubElement(det, "summary"); summ.text = "Hidden section"
            body = etree.SubElement(det, "div", {"class": "det-body"})
            del el.attrib["style"]
            det.tail = el.tail; el.tail = None
            el.getparent().replace(el, det)
            body.append(el)
        else:
            remove(el)
    # images -> tappable placeholders (external images can't load in the app)
    for img in root.xpath(".//img"):
        src = img.get("src") or ""
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = WIKI + src
        if not src.startswith("http"):
            remove(img); continue
        alt = (img.get("alt") or "").strip()
        a = etree.Element("a", {"class": "img", "href": src, "target": "_blank", "rel": "noopener"})
        a.text = alt if alt and not re.search(r"\.(png|jpe?g|gif|webp|svg)$", alt, re.I) else "Image"
        a.tail = img.tail
        img.getparent().replace(img, a)
    # attributes, links, footnotes
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        onclick = el.get("onclick") or ""
        for k in list(el.attrib):
            if k.startswith("on") or k in ("width", "height", "align", "bgcolor", "color", "face", "border"):
                del el.attrib[k]
        if el.get("style") is not None:
            s = clean_style(el.get("style"))
            if s:
                el.set("style", s)
            else:
                del el.attrib["style"]
        cl = classes(el)
        keep = cl & KEEP_CLASS
        if el.tag in ("details", "summary") or cl & {"acs", "acs-c", "tabset", "tabbar", "tab", "on", "pane", "img", "det-body"}:
            pass
        elif keep:
            el.set("class", " ".join(sorted(keep)))
        elif "class" in el.attrib:
            del el.attrib["class"]
        idv = el.get("id") or ""
        if idv and not idv.startswith(("footnote-", "footnoteref-")):
            del el.attrib["id"]
        if el.tag == "a":
            m = FN_RE.search(onclick)
            href = el.get("href") or ""
            if m:
                el.set("data-fn", m.group(1)); el.set("href", "#")
                continue
            if href.startswith("javascript") or not href:
                el.attrib.pop("href", None); continue
            if href.startswith("#"):
                continue
            slug = None
            if href.startswith("/") and not href.startswith("//"):
                slug = href.lstrip("/")
            else:
                mm = re.match(r"https?://([^/]+)/(.*)", href)
                if mm and mm.group(1).lower() in WIKI_HOSTS:
                    slug = mm.group(2)
            if slug is not None and el.get("class") != "img":
                slug = slug.split("#")[0].split("/")[0].lower()
                el.set("data-w", slug)
                el.set("href", f"https://scp-wiki.wikidot.com/{slug}")
            el.set("target", "_blank"); el.set("rel", "noopener")
    out = "".join(lxml.html.tostring(ch, encoding="unicode") for ch in root) if root is pc else lxml.html.tostring(root, encoding="unicode")
    out = (root.text or "") + out if root is pc else out
    out = re.sub(r"\n\s*\n+", "\n", out)
    out = re.sub(r"<p>\s*(&nbsp;|\xa0)?\s*</p>", "", out)
    return out.strip()

