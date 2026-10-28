"""Getting clips into the library: stock footage from Pexels (free licence, a key you get yourself), or a URL you give together
with the licence it is under. Downloads go through the same guarded fetcher as the rest of Minty (public addresses only, size cap),
are checked to be real media before they are kept, and are recorded with their licence. Nothing is ripped from video sites."""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from minty_core.safefetch import FetchFailed, safe_fetch
from minty_core.studio.assets import LICENSES, Asset, AssetError, AssetLibrary
from minty_core.studio.media import MediaError, probe

PEXELS_SEARCH = "https://api.pexels.com/v1/videos/search"
MAX_DOWNLOAD = 150_000_000
URL_LICENSES = ("owned", "cc0", "cc-by", "permission", "pixabay")  # the ones a person can truthfully state for a link


@dataclass(frozen=True)
class Candidate:
    link: str
    width: int
    height: int
    duration: float
    photographer: str
    page: str


def _best_file(files: list[dict]) -> dict | None:
    """Portrait mp4, as sharp as a phone screen needs and no bigger (not the 4K one)."""
    mp4 = [f for f in files if str(f.get("file_type")) == "video/mp4" and f.get("link")]
    portrait = [f for f in mp4 if int(f.get("height") or 0) > int(f.get("width") or 0)] or mp4
    fit = [f for f in portrait if int(f.get("width") or 0) <= 1100] or portrait
    return max(fit, key=lambda f: int(f.get("width") or 0), default=None)


async def pexels_search(query: str, key: str, *, per_page: int = 8) -> list[Candidate]:
    if not key:
        raise AssetError("Stock footage needs a free Pexels key: get one at pexels.com/api, then run `minty set-key plugin.studio.pexels_key`.")
    params = {"query": query[:100], "orientation": "portrait", "size": "medium", "per_page": max(1, min(int(per_page), 20))}
    try:
        got = await safe_fetch("GET", PEXELS_SEARCH + "?" + urlencode(params), auth_headers={"Authorization": key},
                               max_bytes=2_000_000)
    except (FetchFailed, ValueError) as e:
        raise AssetError(f"could not search Pexels: {e}") from None
    if got.status == 401 or got.status == 403:
        raise AssetError("Pexels did not accept the key; check plugin.studio.pexels_key.")
    if got.status != 200:
        raise AssetError(f"Pexels answered with an error ({got.status}).")
    try:
        videos = json.loads(got.data.decode("utf-8", "replace")).get("videos") or []
    except ValueError:
        raise AssetError("Pexels sent something that is not a search result.") from None
    out = []
    for v in videos:
        f = _best_file(v.get("video_files") or [])
        if f:
            out.append(Candidate(str(f["link"]), int(f.get("width") or 0), int(f.get("height") or 0), float(v.get("duration") or 0),
                                 str((v.get("user") or {}).get("name") or "a Pexels creator"), str(v.get("url") or "https://www.pexels.com")))
    return out


async def _download(url: str, expect: tuple[str, ...]) -> bytes:
    if urlsplit(url).scheme not in ("http", "https"):
        raise AssetError("only http and https addresses can be downloaded")
    try:
        got = await safe_fetch("GET", url, max_bytes=MAX_DOWNLOAD)
    except ValueError:
        raise AssetError("that address is not a public one, so I won't fetch it") from None
    except FetchFailed as e:
        raise AssetError(str(e)) from None
    if got.status != 200:
        raise AssetError(f"the server answered with an error ({got.status})")
    if got.truncated:
        raise AssetError(f"the file is too large (over {MAX_DOWNLOAD // 1_000_000} MB)")
    ctype = got.content_type.split(";")[0].strip().lower()
    if ctype and ctype != "application/octet-stream" and not ctype.startswith(expect):
        raise AssetError(f"that is not a {'/'.join(e.rstrip('/') for e in expect)} file (it is {ctype})")
    return got.data


async def _keep(lib: AssetLibrary, data: bytes, name: str, kind: str, license: str, *, source: str, attribution: str = "") -> Asset:
    with tempfile.TemporaryDirectory(prefix="studio-dl-") as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        try:
            await probe(path)  # a real media file, not a page or junk named .mp4
        except MediaError as e:
            raise AssetError(str(e)) from None
        return lib.add(path, kind, license, source=source, attribution=attribution)


async def fetch_stock(lib: AssetLibrary, query: str, key: str, kind: str = "video") -> Asset:
    found = await pexels_search(query, key, per_page=5)
    if not found:
        raise AssetError(f"Pexels has nothing for {query!r}; try other words.")
    last = None
    for c in found:  # the first one that downloads and is real
        try:
            data = await _download(c.link, ("video/",))
            return await _keep(lib, data, Path(urlsplit(c.link).path).name or "clip.mp4", kind, "pexels",
                               source=c.page, attribution=f"Video by {c.photographer} on Pexels")  # no link: a link in a caption makes every post ask first
        except AssetError as e:
            last = e
    raise last or AssetError("could not download a clip")


async def fetch_url(lib: AssetLibrary, url: str, kind: str, license: str | None, *, source: str, attribution: str = "") -> Asset:
    if license not in LICENSES or license not in URL_LICENSES:
        raise AssetError(f"state the licence this file is under, one of: {', '.join(URL_LICENSES)} "
                         "(stock clips come from the stock search; I do not rip videos or songs from video sites)")
    expect = ("audio/",) if kind == "music" else ("image/",) if kind == "image" else ("video/", "audio/")
    data = await _download(url, expect)
    return await _keep(lib, data, Path(urlsplit(url).path).name or "file", kind, license, source=source or url, attribution=attribution)
