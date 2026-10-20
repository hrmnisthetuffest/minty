"""Readable text out of web pages, in chunks small enough to quote from."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "template"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "article", "blockquote", "pre", "table"}


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in_title, self._skip = "", False, 0
        self.parts: list[str] = []
        self.main: list[str] = []  # text inside <article> or <main>
        self._main_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("article", "main"):
            self._main_depth += 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("article", "main") and self._main_depth:
            self._main_depth -= 1
        if tag in _BLOCK:
            self._add("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._add(data)

    def _add(self, text: str) -> None:
        self.parts.append(text)
        if self._main_depth:
            self.main.append(text)


def html_to_text(page: str, max_chars: int = 40_000) -> tuple[str, str]:
    """(title, readable text). Prefers the <article>/<main> body when the page has one."""
    reader = _Reader()
    try:
        reader.feed(page or "")
    except Exception:  # noqa: BLE001 — broken markup: take what was read
        pass
    main = _tidy("".join(reader.main))
    body = main if len(main) > 400 else _tidy("".join(reader.parts))
    return _tidy_title(reader.title), body[:max_chars]


def _tidy(text: str) -> str:
    lines = (" ".join(line.split()) for line in html.unescape(text).splitlines())
    return "\n".join(line for line in lines if len(line) > 1)


def _tidy_title(title: str) -> str:
    return " ".join(html.unescape(title).split())[:160]


def chunk_text(text: str, size: int = 1200, overlap: int = 100) -> list[str]:
    """Pieces of about `size` characters cut at sentence or line ends, with a little overlap so a quote that
    straddles a cut is still whole in one of them."""
    text = text.strip()
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start + size // 2, end), text.rfind("\n", start + size // 2, end))
            end = cut + 1 if cut > start else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in out if c]


def normalise(text: str) -> str:
    """For comparing a quote with a chunk: lower case, quotes and dashes unified, whitespace collapsed."""
    text = (text or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[–—−]", "-", text)
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 400]
