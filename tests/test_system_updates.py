"""630 / 631: the update advisor reads the news before it talks about updates, and never applies anything."""
import re
from datetime import datetime

import pytest

from minty_core.system.osinfo import OS
from minty_core.system.runner import Result
from minty_core.system.updates import (advise, check_updates, last_full_upgrade, news_since, parse_apt, parse_arch_news,
                                       parse_pacman_qu)

ARCH = OS("arch", "Arch Linux", "pacman")
MINT = OS("linuxmint", "Linux Mint 22", "apt")

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>NVIDIA 590 driver update requires manual intervention</title><link>https://archlinux.org/news/nvidia-590/</link>
<pubDate>Tue, 13 Oct 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;Before upgrading, remove nvidia-dkms.&lt;/p&gt;</description></item>
<item><title>Old thing</title><link>https://archlinux.org/news/old/</link><pubDate>Mon, 01 Jun 2026 10:00:00 +0000</pubDate>
<description>x</description></item>
<item><title>Plain news</title><link>https://archlinux.org/news/plain/</link><pubDate>Thu, 15 Oct 2026 08:00:00 +0000</pubDate>
<description>no action needed</description></item>
</channel></rss>"""

PACLOG = """[2026-09-01T10:00:00+0530] [PACMAN] Running 'pacman -Syu'
[2026-09-01T10:00:01+0530] [PACMAN] starting full system upgrade
[2026-10-10T09:12:40+0530] [PACMAN] Running 'pacman -Syu'
[2026-10-10T09:12:44+0530] [PACMAN] starting full system upgrade
[2026-10-11T09:00:00+0530] [PACMAN] Running 'pacman -S htop'
"""

QU = "linux 6.12.1-1 -> 6.12.5-1\nhtop 3.3.0-1 -> 3.4.0-1\nnvidia-utils 580.1-1 -> 590.2-1\n"
APT = ("Listing...\nlinux-image-generic/noble-updates 6.8.0-50.51 amd64 [upgradable from: 6.8.0-49.49]\n"
       "htop/noble 3.3.0-1 amd64 [upgradable from: 3.2.2-1]\n")


def test_news_parsing_and_filtering_by_the_last_upgrade():
    items = parse_arch_news(RSS)
    assert [i.title for i in items][0] == "Plain news"  # newest first
    assert "remove nvidia-dkms" in next(i for i in items if "NVIDIA" in i.title).summary
    since = last_full_upgrade(PACLOG)
    assert since == datetime.fromisoformat("2026-10-10T09:12:44+05:30")
    recent = news_since(items, since)
    assert [i.title for i in recent] == ["Plain news", "NVIDIA 590 driver update requires manual intervention"]


def test_an_xml_bomb_is_refused_not_parsed():
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    assert parse_arch_news(bomb) == []
    assert parse_arch_news("not xml at all") == []


def test_pending_updates_parse_for_both_families():
    assert [(p.name, p.old, p.new) for p in parse_pacman_qu(QU)][0] == ("linux", "6.12.1-1", "6.12.5-1")
    assert [p.name for p in parse_apt(APT)] == ["linux-image-generic", "htop"]


def test_the_advice_puts_news_first_and_flags_manual_steps_and_reboots():
    news = news_since(parse_arch_news(RSS), datetime.fromisoformat("2026-10-10T09:12:44+05:30"))
    text = advise(ARCH, news, parse_pacman_qu(QU), db_note="the package list was last synced earlier")
    assert text.index("news") < text.index("updates are waiting")
    assert "manual intervention" in text and "BEFORE" in text
    assert "linux" in text and "restart" in text.lower() and "nvidia-utils" in text
    assert "I do not run updates" in text and "sudo pacman -Syu" in text
    assert "Never run 'pacman -Sy' on its own" in text  # a partial upgrade breaks Arch
    assert not re.search(r"\bapt\b", text)


def test_mint_gets_apt_wording_and_no_arch_news():
    text = advise(MINT, [], parse_apt(APT))
    assert "sudo apt update && sudo apt upgrade" in text and "pacman" not in text and "Arch" not in text
    assert "restart" in text.lower()  # a new kernel


def test_nothing_waiting_is_said_plainly():
    assert "up to date" in advise(ARCH, [], [], db_note="")


class Fake:
    def __init__(self, have=("pacman",), qu=QU):
        self.calls, self.installed, self.qu = [], have, qu

    def have(self, name):
        return name in self.installed

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        if argv[:2] == ["pacman", "-Qu"] or argv[0] == "checkupdates":
            return Result(0, self.qu)
        if argv[:2] == ["apt", "list"]:
            return Result(0, APT)
        return Result(1)


async def test_the_check_reads_news_then_asks_for_a_list_and_never_applies_anything():
    fake = Fake()

    async def fetch_news():
        return RSS

    text = await check_updates(ARCH, fake, fetch_news, PACLOG)
    assert "manual intervention" in text and "6.12.5-1" in text
    assert all(not any(a in ("-S", "-Sy", "-Syu", "upgrade", "install") for a in c) for c in fake.calls)
    assert fake.calls == [["pacman", "-Qu"]]  # checkupdates is not installed: the older, read-only way
    assert "checkupdates" in text  # and it says the better tool exists


async def test_checkupdates_is_preferred_when_installed():
    fake = Fake(have=("pacman", "checkupdates"))

    async def fetch_news():
        return RSS

    await check_updates(ARCH, fake, fetch_news, PACLOG)
    assert fake.calls == [["checkupdates"]]


async def test_news_that_cannot_be_fetched_is_said_not_skipped():
    async def fetch_news():
        raise RuntimeError("offline")

    text = await check_updates(ARCH, Fake(), fetch_news, PACLOG)
    assert "could not read the Arch news" in text and "https://archlinux.org/news/" in text


async def test_mint_asks_apt_for_a_list_only():
    fake = Fake(have=("apt",))
    text = await check_updates(MINT, fake, None, "")
    assert fake.calls == [["apt", "list", "--upgradable"]] and "linux-image-generic" in text


async def test_an_unknown_system_gets_no_commands():
    assert "do not know how to check" in await check_updates(OS(), Fake(), None, "")
