"""Settings, channels and the loop that make the Gmail watcher and the notifier real."""
import asyncio
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

from minty_core.config import ConfigStore
from minty_core.events import EventBus
from minty_core.notifier import Event
from minty_core.notifier_wiring import build_notifier
from tests.plugin_helpers import make_host


def store(tmp_path, **sections):
    s = ConfigStore(tmp_path)
    data = s.load().model_dump(mode="json")
    for name, values in sections.items():
        data[name].update(values)
    s.update(data)
    return s


class Scheduler:
    def __init__(self):
        self.items = []

    def add(self, text, due, **kw):
        self.items.append((text, kw))


def ev(severity="important", **kw):
    base = dict(source="gmail:me", key="t1", severity=severity, title="Mail from Alex: invoice", body="pay it", fingerprint="a", detail_private=True)
    base.update(kw)
    return Event(**base)


def test_the_watcher_is_off_by_default_and_the_settings_are_bounded(tmp_path):
    cfg = store(tmp_path).config
    assert cfg.gmail_watch.enabled is False and cfg.gmail_watch.interval_s >= 300 and cfg.notify.urgent_bypass_quiet is False
    with pytest.raises(Exception):
        store(tmp_path, gmail_watch={"interval_s": 5})  # a mailbox is not polled every few seconds


async def test_the_channels_reach_the_screen_the_voice_the_phone_and_the_digest(tmp_path):
    bus, scheduler = EventBus(), Scheduler()
    queue = bus.subscribe({"notice", "notify", "phone"})
    n = build_notifier(tmp_path / "n.db", store(tmp_path), bus, {"scheduler": scheduler},
                       clock=lambda: 12 * 3600.0, hour_of=lambda t: 12)
    n.publish(ev("important"))
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    kinds = {e["type"]: e for e in events}
    assert set(kinds) == {"notice", "notify", "phone"}
    assert kinds["notify"].get("local") is True and "pay it" in kinds["notify"]["text"]  # said here, with the detail
    assert "pay it" not in kinds["phone"]["text"] and "laptop" in kinds["phone"]["text"].lower()  # the phone gets the title only
    n.publish(ev("notable", key="t2", fingerprint="b"))
    assert scheduler.items and scheduler.items[0][1]["kind"] == "digest" and scheduler.items[0][1]["user_set"] is False


async def test_it_follows_your_quiet_window_and_your_switches(tmp_path):
    bus = EventBus()
    queue = bus.subscribe({"notice", "notify", "phone"})
    quiet = build_notifier(tmp_path / "q.db", store(tmp_path, proactive={"night_from": "22:00", "night_to": "07:00"}), bus, {},
                           clock=lambda: 23 * 3600.0, hour_of=lambda t: 23)
    quiet.publish(ev("important"))
    types = []
    while not queue.empty():
        types.append(queue.get_nowait()["type"])
    assert types == ["notice"]  # night: the screen only
    off = build_notifier(tmp_path / "o.db", store(tmp_path, notify={"phone": False}), bus, {}, clock=lambda: 12 * 3600.0, hour_of=lambda t: 12)
    off.publish(ev("important"))
    types = []
    while not queue.empty():
        types.append(queue.get_nowait()["type"])
    assert "phone" not in types and "notify" in types
    silent = build_notifier(tmp_path / "s.db", store(tmp_path, proactive={"enabled": False}), bus, {}, clock=lambda: 12 * 3600.0, hour_of=lambda t: 12)
    silent.publish(ev("urgent"))
    types = []
    while not queue.empty():
        types.append(queue.get_nowait()["type"])
    assert types == ["notice"]  # "stop doing things by yourself" means nothing is said unprompted


async def test_the_notices_tools_show_resolve_and_report_status(tmp_path):
    services = {}
    host = make_host(tmp_path, services=services)
    n = build_notifier(tmp_path / "notifier.db", store(tmp_path), EventBus(), {}, clock=lambda: 12 * 3600.0, hour_of=lambda t: 12)
    services["notifier"] = n
    from minty_core.gmailwatch import WatchStore
    watch = WatchStore(tmp_path / "gmailwatch.db")
    watch.account("work")
    watch.failed("work", 60, "HTTPError: 502")
    services["gmail_watch_store"] = watch
    n.publish(ev("urgent"))
    host.reload()
    assert {s.name for s in host.tool_specs() if s.name.startswith("notices_")} == {"notices_recent", "notices_resolve", "notices_status"}
    out = await host.call("notices_recent", {})
    assert "urgent" in out and "Mail from Alex" in out
    assert "Marked" in await host.call("notices_resolve", {"key": "t1"}) and "not one" in await host.call("notices_resolve", {"key": "nope"})
    status = await host.call("notices_status", {})
    assert "work" in status and "failed" in status.lower() and "502" in status


async def test_the_loop_polls_only_when_switched_on_and_keeps_the_notifier_moving(tmp_path):
    from minty_core.app import _gmail_loop

    calls = []

    class Watcher:
        async def poll_once(self):
            calls.append("poll")
            return {}

    class Notifier:
        def retry_due(self):
            calls.append("retry")

        def remind_due(self):
            calls.append("remind")

    cfg = store(tmp_path)
    ctx = NS(store=cfg, services={"notifier": Notifier(), "gmail_watcher": Watcher()})
    await _gmail_loop(ctx, first_s=0, rounds=1, sleep=lambda s: asyncio.sleep(0))
    assert calls == ["retry", "remind"]  # off: no mail is read, but a failed delivery is still retried
    data = cfg.config.model_dump(mode="json")
    data["gmail_watch"]["enabled"] = True
    cfg.update(data)
    calls.clear()
    await _gmail_loop(ctx, first_s=0, rounds=1, sleep=lambda s: asyncio.sleep(0))
    assert calls == ["poll", "retry", "remind"]
