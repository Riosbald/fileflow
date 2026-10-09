"""HTML -> readable Markdown-ish text, standard library only.

Goals, in order: (1) never execute or follow anything, (2) keep the page's real content,
(3) drop what is not content, (4) drop what is *hidden from humans but visible to models*.

Fetched pages are untrusted data. A page can hide instructions aimed at a model in
elements a browser never shows (``display:none``, ``hidden``, ``aria-hidden``, HTML
comments) or in invisible Unicode (zero-width characters, bidi controls, the Unicode
"tag" block). Those are removed here and the count is reported. This is hardening, not a
guarantee: visible text can still contain instructions, so fetched text is always
labelled ``provenance: observed`` and should be treated as data, never as commands.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed",
                       "head", "nav", "footer", "aside", "form", "button", "select", "textarea", "dialog", "audio", "video"})
VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"})
BLOCK = frozenset({"p", "div", "section", "article", "main", "header", "li", "ul", "ol", "table", "tr", "blockquote",
                   "pre", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "figure", "figcaption", "details", "summary", "body", "html"})
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Invisible / direction-control characters used to smuggle text past a human reader.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad\U000e0000-\U000e007f]")
_HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|$))", re.I)
_WS = re.compile(r"[ \t\r\f\v]+")


def strip_invisible(text):
    """Return ``(clean_text, removed_count)``."""
    cleaned, n = _INVISIBLE.subn("", text)
    return cleaned, n


class _Extractor(HTMLParser):
    def __init__(self, base_url):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.base = base_url
        self.title = ""
        self._in_title = False
        self.skip = 0                 # depth inside skipped/hidden elements
        self._skip_stack = []         # tag names that opened a skipped region
        self.main_depth = 0           # inside <main>
        self.article_depth = 0        # inside <article>
        self.blocks = []              # (in_main, in_article, text)
        self.cur = []                 # inline fragments of the block being built
        self.prefix = ""              # e.g. "## ", "- ", "> "
        self.pre = 0
        self.pre_buf = []
        self.href = None
        self.in_link = False
        self.link_text = []
        self.cell_row = None          # collecting table cells
        self.list_stack = []
        self.hidden_removed = 0

    # -- helpers ------------------------------------------------------------
    def _flush(self):
        text = "".join(self.cur)
        self.cur = []
        text = _WS.sub(" ", text).strip()
        if text:
            self.blocks.append((self.main_depth > 0, self.article_depth > 0, self.prefix + text))
        self.prefix = ""

    @staticmethod
    def _hidden(attrs):
        d = dict(attrs)
        if "hidden" in d:
            return True
        if (d.get("aria-hidden") or "").strip().lower() == "true":
            return True
        return bool(_HIDDEN_STYLE.search(d.get("style") or ""))

    # -- parser callbacks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
            return
        if self.skip:
            if tag not in VOID:
                self._skip_stack.append(tag)
                self.skip += 1
            return
        # <header> belongs to the site chrome unless it sits inside an article/main
        skip_header = tag == "header" and self.main_depth == 0 and self.article_depth == 0
        if tag in SKIP_TAGS or skip_header or self._hidden(attrs):
            if tag not in VOID:
                self._skip_stack.append(tag)
                self.skip += 1
            if self._hidden(attrs):
                self.hidden_removed += 1
            return
        if tag in ("main", "article"):
            self._flush()
            if tag == "main":
                self.main_depth += 1
            else:
                self.article_depth += 1
        if tag in HEADINGS:
            self._flush()
            self.prefix = "#" * HEADINGS[tag] + " "
        elif tag == "li":
            self._flush()
            self.prefix = "- "
        elif tag == "blockquote":
            self._flush()
            self.prefix = "> "
        elif tag == "pre":
            self._flush()
            self.pre += 1
            self.pre_buf = []
        elif tag == "br":
            self.cur.append("\n")
        elif tag == "tr":
            self._flush()
            self.cell_row = []
        elif tag in ("td", "th") and self.cell_row is not None:
            self.cur = []
        elif tag == "a":
            self.in_link = True
            href = dict(attrs).get("href") or ""
            href = href.strip()
            if href and not href.lower().startswith(("javascript:", "data:", "vbscript:", "#", "mailto:")):
                self.href = urljoin(self.base, href)
                if not self.href.lower().startswith(("http://", "https://")):
                    self.href = None
            self.link_text = []
        elif tag == "code" and not self.pre:
            self.cur.append("`")
        elif tag in BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.skip:
            self.cur.append("\n")
        elif tag == "hr" and not self.skip:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
            return
        if self.skip:
            if self._skip_stack and tag == self._skip_stack[-1]:
                self._skip_stack.pop()
                self.skip -= 1
            elif tag in self._skip_stack:  # tolerate sloppy HTML: unwind to the matching tag
                while self._skip_stack and self._skip_stack.pop() != tag:
                    self.skip -= 1
                self.skip -= 1
            return
        if tag in ("main", "article"):
            self._flush()
            if tag == "main":
                self.main_depth = max(0, self.main_depth - 1)
            else:
                self.article_depth = max(0, self.article_depth - 1)
        elif tag == "pre":
            text = "".join(self.pre_buf).strip("\n")
            self.pre = max(0, self.pre - 1)
            if text.strip():
                self.blocks.append((self.main_depth > 0, self.article_depth > 0, "```\n" + text + "\n```"))
            self.pre_buf = []
        elif tag == "a":
            text = _WS.sub(" ", "".join(self.link_text)).strip()
            if text:
                self.cur.append("[%s](%s)" % (text, self.href) if self.href else text)
            self.href = None
            self.in_link = False
            self.link_text = []
        elif tag == "code" and not self.pre:
            self.cur.append("`")
        elif tag in ("td", "th") and self.cell_row is not None:
            self.cell_row.append(_WS.sub(" ", "".join(self.cur)).strip())
            self.cur = []
        elif tag == "tr" and self.cell_row is not None:
            row = [c for c in self.cell_row if c]
            if row:
                self.blocks.append((self.main_depth > 0, self.article_depth > 0, " | ".join(row)))
            self.cell_row = None
            self.cur = []
        elif tag in HEADINGS or tag in BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip:
            return
        if self.pre:
            self.pre_buf.append(data)
        elif self.in_link:
            self.link_text.append(data)
        else:
            self.cur.append(data)

    # comments, declarations and processing instructions are ignored (hidden-text vector)


def html_to_text(html, base_url=""):
    """Return ``(title, text, stats)``; ``stats`` = ``{"invisible_removed": n, "hidden_elements": m}``."""
    html, invisible = strip_invisible(html)
    ex = _Extractor(base_url)
    ex.feed(html)
    ex.close()
    ex._flush()
    # Which part of the page is "the content"? Most specific wins, as long as it holds real text:
    # <article> (GitHub puts the README in an article inside a <main> that wraps the whole repo page),
    # then <main>, then everything.
    def enough(blocks):
        return sum(len(t) for t in blocks) >= 200

    articles = [t for _m, in_article, t in ex.blocks if in_article]
    mains = [t for in_main, _a, t in ex.blocks if in_main]
    blocks = articles if enough(articles) else mains if enough(mains) else [t for _m, _a, t in ex.blocks]
    # Sites often render every link twice (visible + screen-reader copy): collapse an immediate repeat of a
    # SHORT block. Long blocks are never collapsed: a repeated paragraph may be repeated on purpose.
    deduped = []
    for b in blocks:
        if deduped and b == deduped[-1] and len(b) <= 200:
            continue
        deduped.append(b)
    blocks = deduped
    text = "\n\n".join(blocks)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = _WS.sub(" ", ex.title).strip()
    if not title:
        first = next((b[2:].strip() for b in blocks if b.startswith("# ")), "")
        title = first
    # Links inside link text were collected separately; keep the output single-line per block.
    return title, text, {"invisible_removed": invisible, "hidden_elements": ex.hidden_removed}
