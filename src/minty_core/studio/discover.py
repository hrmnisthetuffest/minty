"""Finding new background footage from providers whose terms allow it, a little at a time.

NASA's image and video library (no key), Pexels and Pixabay (your own free keys). Nothing is scraped, nothing comes from a video site,
and nothing is searched on every video: a provider is asked at most once a day for each style, a few clips are kept per run, and the
total bytes of a run are capped. Everything that comes back is untrusted: addresses must be on the provider's own hosts and go through
the same guarded fetcher as the rest of Minty (public addresses only, redirects checked, size capped); the bytes must be real media
by their first bytes and by ffprobe; a hit that looks like someone else's work is skipped; a file is kept with its licence and credit.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from minty_core.safefetch import FetchFailed, safe_fetch
from minty_core.studio import media
from minty_core.studio.assets import AssetError, AssetLibrary
from minty_core.studio.catalog import CATEGORIES, Catalog
from minty_core.studio.safetext import clean_credit_name, clean_title

PROVIDERS = ("nasa", "pexels", "pixabay")  # keyless first
HOSTS = {  # the only hosts (and, where a host is shared, the only paths) a provider's answers may point us at
    "nasa": {"images-api.nasa.gov": "/", "images-assets.nasa.gov": "/"},
    "pexels": {"api.pexels.com": "/v1/", "videos.pexels.com": "/", "player.vimeo.com": "/external/"},  # Vimeo: where Pexels' own documentation puts its files
    "pixabay": {"pixabay.com": "/", "cdn.pixabay.com": "/"},
}
MAX_CLIP_BYTES = 60_000_000
BG_SECONDS = 60.0  # a background longer than this is cut: it is looped anyway
RUN_BUDGET_BYTES = 200_000_000
MIN_SECONDS, MAX_SECONDS, MIN_WIDTH = 3.0, 900.0, 480
_THIRD_PARTY = re.compile(r"(?i)©|\(c\)\s|copyright|courtesy of|used with permission|licensed from|getty|istock|shutterstock|reuters|associated press")
_NASA_VARIANTS = ("~medium.mp4", "~small.mp4", "~mobile.mp4")  # not ~orig / ~large: those are huge


class Skip(Exception):
    """This one hit is not used; the message says why."""


@dataclass
class Report:
    provider: str
    category: str
    found: int = 0
    kept: int = 0
    bytes: int = 0
    skipped: list = field(default_factory=list)
    error: str = ""


@dataclass
class Cand:
    ident: str
    title: str
    page: str
    credit: str
    license: str
    variants: list  # download addresses, best first
    text: str = ""
    vertical: bool = False


def host_ok(provider: str, url: str) -> bool:
    parts = urlsplit(url)
    prefix = HOSTS[provider].get((parts.hostname or "").lower())
    return parts.scheme == "https" and not parts.username and parts.port in (None, 443) and prefix is not None and parts.path.startswith(prefix)


def _https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


async def _get(provider: str, url: str, *, max_bytes: int, **kw):
    if not host_ok(provider, url):
        raise Skip("an address outside the provider's own hosts")
    host = urlsplit(url).hostname.lower()
    try:
        return await safe_fetch("GET", url, max_bytes=max_bytes, keep_host=host, **kw)
    except ValueError:
        raise Skip("not a public address") from None


async def _json(provider: str, url: str, **kw) -> dict | list:
    got = await _get(provider, url, max_bytes=1_500_000, **kw)
    if got.status == 429:
        raise FetchFailed(f"{provider} says too many requests (429); it will be asked again later")
    if got.status in (401, 403):
        raise FetchFailed(f"{provider} did not accept the key ({got.status}); check it")
    if got.status != 200:
        raise FetchFailed(f"{provider} answered with an error ({got.status})")
    try:
        return json.loads(got.data.decode("utf-8", "replace"))
    except ValueError:
        raise FetchFailed(f"{provider} sent something that is not a search result") from None


# ---- what each provider says ------------------------------------------------------------------------------------------
async def _nasa(query: str, page: int, **_) -> list[Cand]:
    url = "https://images-api.nasa.gov/search?" + urlencode({"q": query, "media_type": "video", "page": page, "page_size": 10})
    items = ((await _json("nasa", url)).get("collection") or {}).get("items") or []
    out = []
    for it in items:
        data = (it.get("data") or [{}])[0]
        nid, title = str(data.get("nasa_id") or ""), str(data.get("title") or "")
        if not nid or not title or data.get("media_type") != "video":
            continue
        text = " ".join(str(data.get(k) or "") for k in ("title", "description", "secondary_creator", "photographer", "location"))
        out.append(Cand(nid, clean_title(title), "https://images.nasa.gov/details/" + quote(nid), "Video: NASA", "nasa", [str(it.get("href") or "")], text))
    return out


async def _nasa_variants(cand: Cand) -> list[str]:
    """The files of one item, from its manifest, smallest sensible first."""
    manifest = _https(cand.variants[0])
    got = await _get("nasa", manifest, max_bytes=400_000)
    try:
        files = [_https(u) for u in json.loads(got.data.decode("utf-8", "replace")) if isinstance(u, str)]
    except ValueError:
        raise Skip("no list of files") from None
    return [u for suffix in _NASA_VARIANTS for u in files if u.endswith(suffix) and host_ok("nasa", u)]


async def _pexels(query: str, page: int, *, key: str, **_) -> list[Cand]:
    url = "https://api.pexels.com/v1/videos/search?" + urlencode({"query": query[:100], "orientation": "portrait", "size": "medium", "per_page": 10, "page": page})
    out = []
    for v in (await _json("pexels", url, auth_headers={"Authorization": key})).get("videos") or []:
        files = [f for f in (v.get("video_files") or []) if f.get("file_type") == "video/mp4" and f.get("link")]
        files.sort(key=lambda f: (int(f.get("width") or 0) > 1100, -int(f.get("width") or 0)))  # up to ~1080 wide, sharpest first
        user = clean_credit_name((v.get("user") or {}).get("name"), "a Pexels creator")  # a stranger's name goes into a public caption
        out.append(Cand(str(v.get("id")), f"pexels {v.get('id')}", str(v.get("url") or "https://www.pexels.com"), f"Video by {user} on Pexels", "pexels",
                        [str(f["link"]) for f in files[:2]], vertical=True))
    return out


async def _pixabay(query: str, page: int, *, key: str, max_bytes: int, **_) -> list[Cand]:
    url = "https://pixabay.com/api/videos/?" + urlencode({"q": query[:100], "per_page": 10, "page": page, "safesearch": "true"})
    out = []
    for h in (await _json("pixabay", url, auth_params={"key": key})).get("hits") or []:
        sizes = [(n, (h.get("videos") or {}).get(n) or {}) for n in ("medium", "small")]
        links = [str(s["url"]) for _n, s in sizes if s.get("url") and int(s.get("size") or 0) <= max_bytes]
        out.append(Cand(str(h.get("id")), clean_title(h.get("tags"), fallback=f"pixabay {h.get('id')}"), str(h.get("pageURL") or "https://pixabay.com"), "", "pixabay",
                        links, text=str(h.get("tags") or "")))
    return out


_SEARCH = {"nasa": _nasa, "pexels": _pexels, "pixabay": _pixabay}


# ---- one provider, one style ---------------------------------------------------------------------------------------
async def _bytes(cand: Cand, provider: str, max_bytes: int) -> bytes:
    urls = await _nasa_variants(cand) if provider == "nasa" else [u for u in cand.variants if host_ok(provider, u)]
    if not urls:
        raise Skip("no usable file on the provider's own hosts")
    for url in urls:
        got = await _get(provider, url, max_bytes=max_bytes)
        if got.status != 200:
            continue
        if got.truncated:  # too big: try the next, smaller file
            continue
        ctype = got.content_type.split(";")[0].strip().lower()
        if ctype and ctype != "application/octet-stream" and not ctype.startswith("video/"):
            raise Skip(f"not a video (it is {ctype})")
        return got.data
    raise Skip("every file is too large or unavailable")


async def discover(lib: AssetLibrary, catalog: Catalog, provider: str, category: str, *, now: datetime | None = None, key: str = "",
                   limit: int = 2, blocked: tuple = (), max_bytes: int = MAX_CLIP_BYTES, force: bool = False) -> Report:
    """Ask one provider for a few clips of one style. Never raises for a provider's mistakes: they are in the report."""
    now = now or datetime.now()
    report = Report(provider, category)
    c = CATEGORIES.get(category)
    terms = (c.queries.get(provider) if c else None) or ()
    if c is None or not c.queries:
        report.error = f"nothing is fetched for {category}: it is only your own recordings"
        return report
    if not terms:
        report.error = f"{provider} has no searches for {category}"
        return report
    if provider in ("pexels", "pixabay") and not key:
        report.error = f"{provider} needs your free key: run `minty set-key plugin.studio.{provider}_key`"
        return report
    if not force:
        ok, why = catalog.due(provider, category, now)
        if not ok:
            report.skipped.append(why)
            return report
    runs = catalog.runs(provider, category)
    query, page = terms[(now.toordinal() + runs) % len(terms)], 1 + (runs // len(terms)) % 3
    try:
        cands = await _SEARCH[provider](query, page, key=key, max_bytes=max_bytes)
    except (FetchFailed, Skip, ValueError) as e:
        report.error = str(e)
        catalog.log_run(provider, category, now, 0, 0, report.error)
        return report
    report.found = len(cands)
    for cand in cands:
        if report.kept >= limit:
            break
        if catalog.known(provider, cand.ident):
            report.skipped.append(f"{cand.ident}: fetched before")
            continue
        if any(w and w.lower() in f"{cand.title} {cand.text}".lower() for w in blocked):
            report.skipped.append(f"{cand.ident}: a blocked word")
            continue
        if provider == "nasa" and _THIRD_PARTY.search(cand.text):
            report.skipped.append(f"{cand.ident}: looks like third party work")
            continue
        try:
            data = await _bytes(cand, provider, max_bytes)
            with tempfile.TemporaryDirectory(prefix="studio-disc-") as tmp:
                path = Path(tmp) / "clip.bin"
                path.write_bytes(data)
                try:
                    info = await media.probe(path)
                except media.MediaError as e:
                    raise Skip(str(e)) from None
                if not info.has_video or not MIN_SECONDS <= info.duration <= MAX_SECONDS or info.width < MIN_WIDTH:
                    raise Skip(f"unsuitable ({info.width}x{info.height}, {info.duration:.0f} s)")
                try:
                    made = await media.make_background(path, Path(tmp) / "bg.mp4", max_seconds=BG_SECONDS)
                except media.MediaError as e:
                    raise Skip(f"could not be prepared ({e})") from None
                if lib.find(made) is not None:
                    raise Skip("already in the library")
                asset = lib.add(made, "video", cand.license, source=cand.page, attribution=cand.credit, note=cand.title[:80])
            quality = (1.0 if info.width >= 1080 else 0.8 if info.width >= 720 else 0.6) * (1.2 if info.height > info.width or cand.vertical else 1.0)
            catalog.tag(asset, category, provider=provider, external_id=cand.ident, quality=quality)
            report.kept += 1
            report.bytes += len(data)
        except (Skip, AssetError, FetchFailed) as e:
            report.skipped.append(f"{cand.ident}: {e}")
    catalog.log_run(provider, category, now, report.found, report.kept, "")
    return report


async def run_all(lib: AssetLibrary, catalog: Catalog, *, keys: dict, now: datetime | None = None, limit: int = 2, blocked: tuple = (),
                  max_bytes: int = MAX_CLIP_BYTES, budget_bytes: int = RUN_BUDGET_BYTES, categories: tuple | None = None) -> list[Report]:
    """Every provider we can use, for every style it has searches for, skipping what was done lately. Bounded by a byte budget."""
    now = now or datetime.now()
    reports, spent = [], 0
    for category, c in CATEGORIES.items():
        for provider in PROVIDERS:
            if categories is not None and category not in categories:
                continue
            if provider not in c.queries or (provider != "nasa" and not keys.get(provider)):
                continue
            if spent >= budget_bytes or not catalog.due(provider, category, now)[0]:
                continue
            report = await discover(lib, catalog, provider, category, now=now, key=keys.get(provider, ""), limit=limit, blocked=blocked,
                                    max_bytes=min(max_bytes, max(1, budget_bytes - spent)))
            spent += report.bytes
            reports.append(report)
    return reports
