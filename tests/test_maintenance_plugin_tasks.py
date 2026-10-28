"""Plugins can ask for work in the daily quiet window; one failing does not stop the others, and a reloaded plugin replaces its task."""
from minty_core.app import plugin_maintenance


class Ctx:
    def __init__(self, tasks):
        self.services = {"maintenance_tasks": tasks}


async def test_each_registered_task_runs_and_its_words_are_reported():
    async def a():
        return "2 new"

    ran = await plugin_maintenance(Ctx({"studio": a, "other": lambda: "fine"}))()
    assert "studio: 2 new" in ran and "other: fine" in ran


async def test_a_task_that_fails_is_named_and_the_rest_still_run():
    def bad():
        raise RuntimeError("boom")

    ran = await plugin_maintenance(Ctx({"bad": bad, "good": lambda: "ok"}))()
    assert "bad: failed (RuntimeError)" in ran and "good: ok" in ran


async def test_no_tasks_is_not_an_error():
    assert await plugin_maintenance(Ctx({}))() == "nothing registered"


async def test_nothing_is_fetched_or_run_for_plugins_during_a_lockdown():
    from types import SimpleNamespace

    ran = []
    ctx = Ctx({"studio": lambda: ran.append(1) or "2 new"})
    ctx.services["lockdown"] = SimpleNamespace(active=True)
    assert "lockdown" in await plugin_maintenance(ctx)() and ran == []
    ctx.services["lockdown"] = SimpleNamespace(active=False)
    assert "studio: 2 new" in await plugin_maintenance(ctx)() and ran == [1]
