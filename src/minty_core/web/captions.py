"""YouTube captions as text (568). Captions only, never the video, through `yt-dlp` if you have installed it, started by you
for one address. This reads a site's public subtitles with a third-party tool; its terms are a grey area, so it is labelled
and off unless `web.helpers` is on."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_SAFE = re.compile(r"[A-Za-z0-9:/._?=&%#+-]+")
_TAG = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}\s*-->")


class CaptionsUnavailable(Exception):
    pass


def valid_video_url(url: str) -> str:
    url = str(url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in YT_HOSTS \
            or parts.username or not _SAFE.fullmatch(url):
        raise ValueError("Give a YouTube video address.")
    return url


def parse_vtt(text: str) -> str:
    kept: list[str] = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or _STAMP.match(line) or line.isdigit():
            continue
        line = " ".join(_TAG.sub("", line).replace("&nbsp;", " ").replace("&amp;", "&").split())
        if line and (not kept or line != kept[-1]):
            kept.append(line)
    return " ".join(kept)


async def fetch_captions(url: str, run: Callable[[list[str], Path], Awaitable[tuple[int, str]]], folder: Path,
                         which: Callable[[str], str | None] = shutil.which) -> str:
    url = valid_video_url(url)
    tool = which("yt-dlp")
    if not tool:
        raise CaptionsUnavailable("yt-dlp is not installed. It is a free tool you install yourself (for example "
                                  "'pipx install yt-dlp'); I will not install it without your yes.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [tool, "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,hi", "--sub-format",
           "vtt", "--no-playlist", "-o", str(folder / "%(id)s.%(ext)s"), url]
    code, _out = await run(cmd, folder)
    files = sorted(folder.glob("*.vtt"), key=lambda p: (".en" not in p.name, p.name))
    if code != 0 and not files:
        raise CaptionsUnavailable("I could not get the captions for that video.")
    if not files:
        raise CaptionsUnavailable("That video has no captions I can read.")
    text = parse_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
    for f in files:
        f.unlink(missing_ok=True)
    return text
