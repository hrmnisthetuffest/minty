"""The update advisor (631) with the Arch news check (630). It reads the news since your last full upgrade FIRST, then what is
waiting, and tells you what to type. It never runs an update, an install or a sync. `pacman -Qu` only knows the package list
from the last sync, and the advice says so; `checkupdates` (pacman-contrib) is better and is used when present."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

from minty_core.system.osinfo import OS, update_command

NEWS_URL = "https://archlinux.org/news/"
NEWS_FEED = "https://archlinux.org/feeds/news/"
ARCH_NOTABLE = {"linux", "linux-lts", "linux-zen", "linux-hardened", "linux-firmware", "systemd", "glibc", "grub",
                "mesa", "xorg-server", "pipewire", "cinnamon", "python", "openssl", "pacman", "gcc-libs", "networkmanager"}
NEEDS_RESTART = ("linux", "nvidia", "systemd", "glibc", "libc6", "linux-image")
_TAGS = re.compile(r"<[^>]+>")
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})\]")


@dataclass
class NewsItem:
    title: str
    link: str
    date: datetime
    summary: str = ""


@dataclass
class Pending:
    name: str
    old: str
    new: str


def parse_arch_news(xml_text: str) -> list[NewsItem]:
    if re.search(r"<!DOCTYPE|<!ENTITY", str(xml_text), re.I):
        return []  # entity tricks have no place in a news feed
    try:
        root = ET.fromstring(str(xml_text))
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        summary = " ".join(html.unescape(_TAGS.sub(" ", it.findtext("description") or "")).split())
        items.append(NewsItem(" ".join((it.findtext("title") or "").split()), (it.findtext("link") or "").strip(), when,
                              summary[:300]))
    return sorted(items, key=lambda i: -i.date.timestamp())


def last_full_upgrade(pacman_log: str) -> datetime | None:
    last = None
    for line in str(pacman_log).splitlines():
        if "starting full system upgrade" in line:
            m = _STAMP.match(line)
            if m:
                last = datetime.fromisoformat(f"{m.group(1)}{m.group(2)}:{m.group(3)}")
    return last


def news_since(items: list[NewsItem], since: datetime | None, limit: int = 5, default_days: int = 45,
               now: datetime | None = None) -> list[NewsItem]:
    cutoff = since or ((now or datetime.now(timezone.utc)) - timedelta(days=default_days))
    return [i for i in items if i.date >= cutoff][:limit]


def parse_pacman_qu(out: str) -> list[Pending]:
    found = []
    for line in str(out).splitlines():
        m = re.match(r"^(\S+) (\S+) -> (\S+)", line.strip())
        if m:
            found.append(Pending(*m.groups()))
    return found


def parse_apt(out: str) -> list[Pending]:
    found = []
    for line in str(out).splitlines():
        m = re.match(r"^([^/\s]+)/\S+ (\S+) \S+ \[upgradable from: (\S+)\]", line.strip())
        if m:
            found.append(Pending(m.group(1), m.group(3), m.group(2)))
    return found


def _notable(os_: OS, p: Pending) -> bool:
    if os_.manager == "pacman":
        return p.name in ARCH_NOTABLE or p.name.startswith("nvidia")
    return p.name.startswith(("linux-image", "nvidia", "libc6", "systemd", "grub", "mesa", "cinnamon"))


def advise(os_: OS, news: list[NewsItem], pending: list[Pending], db_note: str = "") -> str:
    parts: list[str] = []
    if os_.manager == "pacman":
        if news:
            lines = []
            for i, n in enumerate(news, 1):
                flag = ""
                if "manual intervention" in n.title.lower() or "manual intervention" in n.summary.lower():
                    flag = " This needs manual intervention: do what it says BEFORE updating."
                lines.append(f"{i}. {n.title} ({n.date:%-d %b}).{flag}" + (f" {n.summary}" if flag and n.summary else ""))
            parts.append(f"Arch news since your last full update: {len(news)} item{'s' * (len(news) != 1)}. Read these "
                         "before updating.\n" + "\n".join(lines))
        else:
            parts.append("No Arch news since your last full update.")
    if not pending:
        parts.append("Nothing is waiting: you are up to date" + (" as of the last package sync." if os_.manager == "pacman" else "."))
    else:
        notable = [p for p in pending if _notable(os_, p)][:6]
        text = f"{len(pending)} updates are waiting."
        if notable:
            text += " Notable: " + ", ".join(f"{p.name} {p.old} → {p.new}" for p in notable) + "."
        if any(p.name.startswith(NEEDS_RESTART) for p in pending):
            text += " A restart will be needed afterwards (new kernel, driver or core library)."
        parts.append(text + (f" {db_note}" if db_note else ""))
    command = update_command(os_)
    tail = f"I do not run updates. When you are ready, in a terminal: {command}."
    if os_.manager == "pacman":
        tail += " Never run 'pacman -Sy' on its own: a partial upgrade can break Arch."
    parts.append(tail)
    return "\n".join(parts)


async def check_updates(os_: OS, runner, fetch_news: Callable[[], Awaitable[str]] | None, pacman_log: str) -> str:
    if os_.manager == "pacman":
        news: list[NewsItem] = []
        note = ""
        try:
            news = news_since(parse_arch_news(await fetch_news()), last_full_upgrade(pacman_log)) if fetch_news else []
        except Exception:  # noqa: BLE001 — say it, do not pretend there was no news
            note = f"I could not read the Arch news just now; check {NEWS_URL} before you update.\n"
        if runner.have("checkupdates"):
            res = await runner.run(["checkupdates"])
            db_note = ""
        else:
            res = await runner.run(["pacman", "-Qu"])
            db_note = ("This list comes from the last package sync and may be out of date; 'checkupdates' "
                       "(package pacman-contrib) asks without changing anything.")
        if res.missing:
            return note + "pacman is not available here."
        return note + advise(os_, news, parse_pacman_qu(res.out), db_note)
    if os_.manager == "apt":
        res = await runner.run(["apt", "list", "--upgradable"])
        return advise(os_, [], parse_apt(res.out)) if not res.missing else "apt is not available here."
    return "I do not know how to check for updates on this system."
