"""Price, stock and title from a product page (JSON-LD first, then meta tags and itemprop), and a text fingerprint
that ignores scripts, clocks and the copyright year so a page that only re-rendered does not look changed."""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:product:price:amount|og:price:amount|twitter:data1)["\']'
                   r'[^>]*content=["\']([^"\']+)["\']', re.I)
_META_REV = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']'
                       r'(?:product:price:amount|og:price:amount)["\']', re.I)
_ITEMPROP = re.compile(r'<[^>]+itemprop=["\']price["\'][^>]*>', re.I)
_ITEMPROP_BLOCK = re.compile(r'<(\w+)[^>]+itemprop=["\']price["\'][^>]*>(.*?)</\1>', re.S | re.I)
_CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_CURRENCY = re.compile(r"(?:rs\.?|inr|usd|eur|gbp|[₹$€£])", re.I)
_NUM = re.compile(r"\d[\d.,]*")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*\d{4}(?:\s*-\s*\d{4})?", re.I)


@dataclass
class Product:
    name: str = ""
    price: float | None = None
    currency: str = ""
    in_stock: bool | None = None


def parse_price(text: str) -> float | None:
    m = _NUM.search(_CURRENCY.sub(" ", str(text)))
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}(\.\d{3})+", s) and s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _stock(value) -> bool | None:
    text = str(value or "").lower()
    if not text:
        return None
    if any(w in text for w in ("outofstock", "soldout", "discontinued", "out_of_stock")):
        return False
    if any(w in text for w in ("instock", "limitedavailability", "onlineonly", "preorder", "in_stock")):
        return True
    return None


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _offers(product: dict) -> list[dict]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        offers = [offers]
    out = []
    for o in offers or []:
        if isinstance(o, dict):
            out.append(o)
            out.extend(x for x in (o.get("offers") or []) if isinstance(x, dict))
    return out


def _from_json_ld(page: str) -> Product | None:
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for node in _walk(data):
            kinds = node.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if "Product" not in kinds:
                continue
            best: tuple[float, dict] | None = None
            for o in _offers(node):
                price = parse_price(o.get("price", o.get("lowPrice", ""))) if o.get("price", o.get("lowPrice")) \
                    not in (None, "") else None
                if price is not None and (best is None or price < best[0]):
                    best = (price, o)
            stocks = [_stock(o.get("availability")) for o in _offers(node)]
            known = [s for s in stocks if s is not None]
            return Product(html.unescape(str(node.get("name") or "")), best[0] if best else None,
                           str(best[1].get("priceCurrency") or "") if best else "",
                           (any(known) if known else None))
    return None


def product_from_html(page: str) -> Product:
    found = _from_json_ld(page)
    if found is not None and found.price is not None:
        return found
    price = None
    for rx in (_META, _META_REV):
        m = rx.search(page)
        if m:
            price = parse_price(m.group(1))
            break
    if price is None:
        tag = _ITEMPROP.search(page)
        if tag:
            c = _CONTENT.search(tag.group(0))
            price = parse_price(c.group(1)) if c else None
            if price is None:
                block = _ITEMPROP_BLOCK.search(page)
                price = parse_price(re.sub(r"<[^>]+>", " ", block.group(2))) if block else None
    title = _TITLE.search(page)
    base = found or Product()
    return Product(base.name or (html.unescape(" ".join(title.group(1).split())) if title else ""), price,
                   base.currency, base.in_stock)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "section"):
            self.parts.append(". " if tag in ("li",) else " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def page_text(page: str) -> str:
    parser = _Text()
    try:
        parser.feed(page)
        parser.close()
    except Exception:  # noqa: BLE001 — broken markup: keep what was read
        pass
    return " ".join("".join(parser.parts).split())


def fingerprint(page: str) -> tuple[str, str]:
    """(hash, text) of the visible text with clocks and copyright years removed."""
    text = _CLOCK.sub("", _COPYRIGHT.sub("", page_text(page)))
    text = " ".join(text.split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def changed_sentences(old: str, new: str, limit: int = 6) -> tuple[list[str], list[str]]:
    split = lambda t: [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]  # noqa: E731
    a, b = split(old), split(new)
    return [s for s in b if s not in a][:limit], [s for s in a if s not in b][:limit]
