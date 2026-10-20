"""The system loop in app.py: alerts only while their switch is on; the maintenance gate only while its switch is on."""
import asyncio
from types import SimpleNamespace

import pytest

from minty_core.config import Config
from minty_core.system.kit import SystemProvider
from minty_core.system.runner import Result


REAL_SLEEP = asyncio.sleep


class Stop(Exception):
    pass


def ctx_for(cfg, tmp_path):
    events = []
    provider = SystemProvider(lambda: cfg, tmp_path, {}, proc_root=tmp_path / "proc")
    (tmp_path / "proc").mkdir()
    ctx = SimpleNamespace(store=SimpleNamespace(config=cfg), services={"system": provider},
                          bus=SimpleNamespace(publish=lambda kind, **kw: events.append((kind, kw))))
    return ctx, events


async def one_pass(ctx, monkeypatch):
    from minty_core import app

    async def sleep(_s):
        raise Stop

    monkeypatch.setattr(app.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        await app._system_loop(ctx)


async def test_a_low_battery_is_said_once_when_alerts_are_on(tmp_path, monkeypatch):
    cfg = Config()
    cfg.system.alerts = True
    ctx, events = ctx_for(cfg, tmp_path)
    monkeypatch.setattr("minty_core.power.read_battery", lambda: (False, 15))
    monkeypatch.setattr("minty_core.power.read_temperature", lambda: 50.0)
    await one_pass(ctx, monkeypatch)
    assert ("notify", {"text": "The battery is at 15 percent and not charging."}) in events
    n = len(events)
    await one_pass(ctx, monkeypatch)
    assert len(events) == n  # the same condition does not repeat


async def test_nothing_is_read_or_said_with_the_switch_off(tmp_path, monkeypatch):
    ctx, events = ctx_for(Config(), tmp_path)
    monkeypatch.setattr("minty_core.power.read_battery", lambda: (_ for _ in ()).throw(AssertionError("must not read")))
    await one_pass(ctx, monkeypatch)
    assert events == []


async def test_the_notification_listener_starts_only_for_chosen_apps(tmp_path, monkeypatch):
    from minty_core import app

    cfg = Config()
    cfg.system.notifications = True
    ctx, _events = ctx_for(cfg, tmp_path)
    started = []

    async def fake_monitor(reader, **kw):
        started.append(reader)
        await asyncio.Event().wait()

    monkeypatch.setattr("minty_core.system.notify.monitor", fake_monitor)
    await one_pass(ctx, monkeypatch)
    assert started == []  # notifications on but no app chosen: nothing is listened to
    cfg.system.notification_apps = ["telegram"]
    await one_pass(ctx, monkeypatch)
    await REAL_SLEEP(0)
    assert len(started) == 1
