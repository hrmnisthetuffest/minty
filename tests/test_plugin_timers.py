import asyncio

from tests.plugin_helpers import make_host


async def test_timer_fires_notification_and_clears(tmp_path):
    heard = []
    host = make_host(tmp_path, notify=heard.append)
    assert "Timer 1" in await host.call("timers_set_timer", {"seconds": 0.05, "label": "tea"})
    assert "Timer 1 (tea)" in await host.call("timers_list_timers", {})
    await asyncio.sleep(0.2)
    assert heard == ["Your tea timer is done."]
    assert await host.call("timers_list_timers", {}) == "No timers running."


async def test_unlabelled_timer_message(tmp_path):
    heard = []
    host = make_host(tmp_path, notify=heard.append)
    await host.call("timers_set_timer", {"seconds": 0.05})
    await asyncio.sleep(0.2)
    assert heard == ["Your timer is done."]


async def test_cancel_prevents_notification(tmp_path):
    heard = []
    host = make_host(tmp_path, notify=heard.append)
    await host.call("timers_set_timer", {"seconds": 0.05, "label": "pasta"})
    assert "Cancelled" in await host.call("timers_cancel_timer", {"id": 1})
    await asyncio.sleep(0.2)
    assert heard == []
    assert "No timer" in await host.call("timers_cancel_timer", {"id": 1})


async def test_bad_durations_are_rejected(tmp_path):
    host = make_host(tmp_path)
    for bad in (0, -3, 999999):
        assert (await host.call("timers_set_timer", {"seconds": bad})).startswith("Error: ValueError")
    assert await host.call("timers_list_timers", {}) == "No timers running."


async def test_running_timer_survives_host_reload(tmp_path):
    heard = []
    host = make_host(tmp_path, notify=heard.append)
    await host.call("timers_set_timer", {"seconds": 0.15, "label": "oven"})
    host.reload()
    assert "oven" in await host.call("timers_list_timers", {})
    await asyncio.sleep(0.3)
    assert heard == ["Your oven timer is done."]
