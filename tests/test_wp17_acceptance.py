"""WP-17 acceptance (plan-master):
* on an Arch fixture no Ubuntu command is ever suggested (and the other way round);
* the update advisor lists news before updates and never runs an update;
* the notification reader stores nothing and speaks only when asked."""
import re

from minty_core.system.notify import Note
from tests.test_system_plugin import ARCH, MINT, Rig


async def everything_said(r):
    outs = [await r.call("info", {"action": "os"}),
            await r.call("diagnose", {"action": "updates"}),
            await r.call("usb", {"action": "list"}),
            await r.call("script", {"packages": ["playerctl", "pulseaudio-utils", "x11-utils", "tesseract-ocr",
                                                 "pacman-contrib"]})]
    return "\n".join(outs)


async def test_arch_never_hears_apt_and_mint_never_hears_pacman(tmp_path):
    arch = await everything_said(Rig(tmp_path / "a", os_info=ARCH))
    assert not re.search(r"\b(apt|apt-get|dpkg|ppa)\b", arch, re.I), arch
    assert "pacman" in arch
    mint_rig = Rig(tmp_path / "m", os_info=MINT)
    from minty_core.system.runner import Result
    from tests.test_system_updates import APT

    mint_rig.runner.answers = {"apt list": Result(0, APT)}
    mint = await everything_said(mint_rig)
    assert not re.search(r"\b(pacman|yay|pamac|aur)\b", mint.replace("pacman-contrib is not needed", ""), re.I), mint
    assert "apt install" in mint


async def test_the_advisor_reads_the_news_first_and_no_run_ever_contains_an_update_flag(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("diagnose", {"action": "updates"})
    assert out.index("news") < out.index("updates are waiting")
    flat = [" ".join(c) for c in r.runner.calls]
    assert flat == ["pacman -Qu"]
    assert not any(re.search(r"\s-S|\bupgrade\b|\binstall\b|\bsync\b", f) for f in flat)


async def test_the_reader_stores_nothing_and_is_silent_until_asked(tmp_path, monkeypatch):
    r = Rig(tmp_path, notifications=True, notification_apps=["telegram"])
    monkeypatch.chdir(tmp_path)
    before = sorted(p.name for p in (tmp_path / "cfg").rglob("*"))
    published = []
    r.host._services["publish"] = lambda *a, **k: published.append((a, k))
    reader = r.host._services["system"].reader()
    reader.feed(Note("Telegram Desktop", "Ravi", "are you coming?"))
    reader.feed(Note("Telegram Desktop", "Ravi", "hello?"))
    assert published == []  # nothing is spoken by itself
    assert sorted(p.name for p in (tmp_path / "cfg").rglob("*")) == before  # nothing written anywhere
    assert "2 notifications" in await r.call("notifications", {"action": "read"})
    assert sorted(p.name for p in (tmp_path / "cfg").rglob("*")) == before
