"""Safe-open for links from mail and chat (570): judge the address first, then read the page as plain text with no cookies
in or out and no scripts run, show where redirects ended, and flag a page that asks for a password. It reads; it never
visits in a browser, never submits anything and never types."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from minty_core.web.extract import page_text

SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "cutt.ly", "rb.gy", "rebrand.ly", "shorturl.at",
              "tiny.cc", "buff.ly", "lnkd.in", "wa.me"}
OFFICIAL = {
    "paypal": {"paypal.com", "paypal.me"},
    "google": {"google.com", "google.co.in", "gstatic.com", "googleapis.com", "googleusercontent.com", "goo.gl"},
    "amazon": {"amazon.com", "amazon.in", "amazon.co.uk", "amazonaws.com", "amazon.de"},
    "flipkart": {"flipkart.com"}, "paytm": {"paytm.com"}, "phonepe": {"phonepe.com"},
    "microsoft": {"microsoft.com", "live.com", "office.com", "microsoftonline.com"},
    "instagram": {"instagram.com"}, "facebook": {"facebook.com", "fb.com"}, "whatsapp": {"whatsapp.com", "wa.me"},
    "netflix": {"netflix.com"}, "irctc": {"irctc.co.in"}, "hdfcbank": {"hdfcbank.com"}, "icicibank": {"icicibank.com"},
    "telegram": {"telegram.org", "t.me", "telegram.me"}, "discord": {"discord.com", "discord.gg"},
    "github": {"github.com", "github.io", "githubusercontent.com"},
}
_LOOK = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "$": "s"})
_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu", "nic"}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_PASSWORD = re.compile(r"""type\s*=\s*["']?password""", re.I)
_RANK = {"ok": 0, "caution": 1, "danger": 2}


@dataclass
class UrlReport:
    verdict: str = "ok"
    reasons: list[str] = field(default_factory=list)
    host: str = ""

    def worst(self, other: "UrlReport") -> "UrlReport":
        return self if _RANK[self.verdict] >= _RANK[other.verdict] else other


def registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def inspect_url(url: str) -> UrlReport:
    url = str(url or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    r = UrlReport(host=host)

    def flag(level: str, why: str) -> None:
        r.verdict = level if _RANK[level] > _RANK[r.verdict] else r.verdict
        r.reasons.append(why)

    if parts.scheme not in ("http", "https"):
        flag("danger", "It is not a web address (only http and https links are opened).")
        return r
    if not host:
        flag("danger", "There is no site in that address.")
        return r
    if parts.username is not None:
        flag("danger", f"The part before the @ is not the site. The real site is {host}.")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            flag("danger", "It points at a computer on your own network, not the internet.")
        else:
            flag("caution", "It is a bare number address, not a site name.")
    except ValueError:
        pass
    if parts.scheme == "http":
        flag("caution", "It is not encrypted (http, not https).")
    if "xn--" in host or not host.isascii():
        flag("caution", "The name uses look-alike characters (it may imitate another site).")
    if host in SHORTENERS or registrable(host) in SHORTENERS:
        flag("caution", "It is a shortened link: it hides where it goes.")
    reg = registrable(host)
    squashed = [label.replace("-", "").translate(_LOOK).replace("rn", "m").replace("vv", "w")
                for label in host.split(".")]
    for brand, official in OFFICIAL.items():
        if any(brand in label for label in squashed) and reg not in official:
            flag("danger", f"It looks like {brand} but belongs to {reg}.")
            break
    if len(host) > 60 or host.count(".") > 4:
        flag("caution", "The name is unusually long.")
    return r


async def fetch_clean(url: str, *, fetch=None, client=None, max_bytes: int = 500_000):
    """A GET with the shared client's cookie jar emptied before and after, so nothing is sent and nothing is kept."""
    if fetch is None:
        from minty_core.safefetch import safe_fetch as fetch
    if client is None:
        from minty_core.httpclient import get_client

        client = get_client()
    client.cookies.clear()
    try:
        return await fetch("GET", url, max_bytes=max_bytes)
    finally:
        client.cookies.clear()


@dataclass
class Opened:
    opened: bool
    report: UrlReport
    title: str = ""
    text: str = ""
    final_host: str = ""
    asks_for_password: bool = False
    note: str = ""
    redirected: bool = False

    def summary(self) -> str:
        parts: list[str] = []
        if not self.opened and self.report.verdict == "danger" and not self.final_host:
            return "I did not open it. " + " ".join(self.report.reasons)
        if self.redirected:
            parts.append(f"It leads to a different site: {self.final_host}.")
        if self.report.verdict != "ok":
            parts.append(("Danger: " if self.report.verdict == "danger" else "Careful: ") + " ".join(self.report.reasons))
        if self.asks_for_password:
            parts.append("The page asks for a password. Do not enter one from a link; go to the site yourself.")
        if self.note:
            parts.append(self.note)
        if self.opened:
            parts.append(f"Title: {self.title or 'none'}. {self.text}")
        return " ".join(parts) or "I could not read it."


async def safe_open(url: str, fetch: Callable[[str], Awaitable], excerpt: int = 3000) -> Opened:
    report = inspect_url(url)
    if report.verdict == "danger":
        return Opened(False, report)
    try:
        got = await fetch(url)
    except Exception:  # noqa: BLE001 — the reason is not worth leaking; the address is already shown to the user
        return Opened(False, report, note="I could not reach it.")
    final = getattr(got, "url", "") or url
    final_host = (urlsplit(final).hostname or "").lower()
    redirected = bool(final_host) and final_host != report.host
    if redirected:
        report = report.worst(inspect_url(final))
        if report.host != final_host:
            report = UrlReport(report.verdict, report.reasons, final_host)
    ctype = (got.content_type or "").lower()
    if got.status >= 400:
        return Opened(False, report, final_host=final_host, redirected=redirected,
                      note=f"The site answered with an error ({got.status}).")
    if not (ctype.startswith("text/html") or ctype.startswith("application/xhtml") or ctype.startswith("text/plain")):
        return Opened(False, report, final_host=final_host, redirected=redirected,
                      note=f"That is not a web page ({ctype.split(';')[0] or 'unknown type'}, {len(got.data)} bytes); "
                           "I did not read it.")
    body = got.data.decode(got.charset or "utf-8", "replace")
    title = _TITLE.search(body)
    asks = bool(_PASSWORD.search(body))
    text = page_text(body)[:excerpt]
    shown = report.verdict != "danger"
    return Opened(shown, report, " ".join(title.group(1).split())[:160] if title else "", text if shown else "",
                  final_host, asks, "" if shown else "I am not showing the page text.", redirected)
