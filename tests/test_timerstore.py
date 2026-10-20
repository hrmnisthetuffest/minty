"""Durable, repeating, named timers (580 600)."""
import asyncio
import json

from tests.plugin_helpers import make_host
from minty_core.timerstore import TimerStore


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_a_timer_is_kept_on_disk_with_its_due_time_and_survives_a_new_process(tmp_path):
    clock = Clock()
    s = TimerStore(tmp_path / "timers.json", clock=clock)
    t = s.add(600, "pasta")
    assert t.id == 1 and t.due == 1600
    clock.t += 100
    again = TimerStore(tmp_path / "timers.json", clock=clock)  # "restart"
    only = again.all()[0]
    assert only.label == "pasta" and round(only.remaining(clock())) == 500
    assert again.add(5, "x").id == 2  # ids keep counting


def test_the_file_is_owner_only(tmp_path):
    TimerStore(tmp_path / "timers.json").add(60, "t")
    assert oct((tmp_path / "timers.json").stat().st_mode & 0o777) == "0o600"


def test_a_repeating_timer_fires_again_and_a_one_off_goes(tmp_path):
    clock = Clock()
    s = TimerStore(tmp_path / "timers.json", clock=clock)
    once, rep = s.add(10, "tea"), s.add(10, "stretch", repeat_s=1800)
    clock.t += 11
    fired = s.fire_due()
    assert {f.label for f in fired} == {"tea", "stretch"}
    assert [t.label for t in s.all()] == ["stretch"] and s.all()[0].due == 1000 + 11 + 1800
    assert s.fire_due() == []


def test_a_timer_that_came_due_while_off_is_flagged_late(tmp_path):
    clock = Clock()
    s = TimerStore(tmp_path / "timers.json", clock=clock)
    s.add(60, "oven")
    clock.t += 3600
    fired = s.fire_due()
    assert fired[0].late is True and s.all() == []


def test_a_damaged_file_is_set_aside_not_a_crash(tmp_path):
    (tmp_path / "timers.json").write_text("{not json")
    s = TimerStore(tmp_path / "timers.json")
    assert s.all() == [] and s.add(5, "ok").id == 1
    assert (tmp_path / "timers.json.bad").exists()


def test_limits(tmp_path):
    import pytest

    s = TimerStore(tmp_path / "timers.json")
    for bad in (0, -1, 999999):
        with pytest.raises(ValueError):
            s.add(bad, "x")
    with pytest.raises(ValueError):
        s.add(60, "x", repeat_s=5)  # a repeat under a minute is a nuisance, not a timer
    assert s.cancel(99) is None


async def test_the_plugin_survives_a_restart_and_says_a_missed_timer_was_late(tmp_path):
    heard = []
    host = make_host(tmp_path, notify=heard.append)
    await host.call("timers_set_timer", {"seconds": 3600, "label": "oven"})
    data = json.loads((tmp_path / "timers.json").read_text())
    data["timers"][0]["due"] -= 4000  # the app was off for over an hour
    (tmp_path / "timers.json").write_text(json.dumps(data))
    heard.clear()
    host2 = make_host(tmp_path, notify=heard.append)  # a fresh start
    await host2.call("timers_list_timers", {})  # the first call re-arms what is on disk
    await asyncio.sleep(0.1)
    assert heard and "oven" in heard[0] and "while" in heard[0]


async def test_repeat_and_kind_are_options_of_the_set_tool(tmp_path):
    host = make_host(tmp_path)
    out = await host.call("timers_set_timer", {"seconds": 1500, "label": "study", "repeat_seconds": 0, "kind": "study"})
    assert "Timer 1" in out and "study" in await host.call("timers_list_timers", {})


def test_the_api_lists_running_timers_for_the_overlay(tmp_path):
    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    store = TimerStore(tmp_path / "timers.json", clock=lambda: 1_000.0)
    store.add(300, "tea", kind="study")
    ctx.services["timers"] = store
    client = TestClient(create_app(ctx))
    rows = client.get("/timers", headers={"Authorization": f"Bearer {ctx.token}"}).json()
    assert rows[0]["label"] == "tea" and rows[0]["kind"] == "study" and rows[0]["remaining_s"] == 300
