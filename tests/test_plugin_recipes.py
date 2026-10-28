from datetime import datetime
from types import SimpleNamespace

from tests.plugin_helpers import make_host

DAY = datetime(2026, 10, 28, 18, 5)


def build(tmp_path, **cfg):
    started = []
    jobs = SimpleNamespace(on_finish=[], register_resumer=lambda *a: None, select=lambda **k: [])

    def start_mission(goal, extra):
        started.append((goal, extra))
        return len(started)

    host = make_host(tmp_path, plugins={"recipes": {"enabled": True, **cfg}, "missions": {"enabled": False}},
                     services={"start_mission": start_mission, "jobs": jobs})
    return host, started, jobs


async def create(host, **kw):
    args = {"name": "daily-reel", "goal": "make today's reel and queue it for my approval", "at": "18:00", **kw}
    return await host.call("recipes_create", args)


async def test_creating_a_standing_automation_is_always_asked_with_the_whole_plan_shown(tmp_path):
    host, _, _ = build(tmp_path)
    tool = host.get_tool("recipes_create")
    assert tool.risk == 3  # a standing rule that spends model time every day: asked whatever the safety mode
    text = tool.describe({"name": "daily-reel", "goal": "make today's reel", "at": "18:00", "days": "daily"})
    assert "make today's reel" in text and "18:00" in text and "every day" in text.lower() and "each risky step" in text.lower()
    assert "Created" in await create(host)
    assert "already" in await create(host)


async def test_a_bad_recipe_is_an_error_message_not_a_crash(tmp_path):
    host, _, _ = build(tmp_path)
    out = await create(host, at="6pm")
    assert out.startswith("Error") and "HH:MM" in out


async def test_the_list_shows_schedule_state_and_recent_runs(tmp_path):
    host, started, jobs = build(tmp_path)
    await create(host)
    assert "daily-reel" in await host.call("recipes_list", {}) and "18:00" in await host.call("recipes_list", {})
    out = await host.call("recipes_run_now", {"name": "daily-reel"})
    assert "Started" in out and started[0][1]["recipe"] == "daily-reel"
    detail = await host.call("recipes_list", {"name": "daily-reel"})
    assert "started" in detail and "make today's reel" in detail


async def test_when_the_mission_ends_the_run_is_closed_and_failures_pause_the_recipe(tmp_path):
    host, started, jobs = build(tmp_path)
    await create(host, max_failures=1)
    await host.call("recipes_run_now", {"name": "daily-reel"})
    (listener,) = jobs.on_finish
    listener(SimpleNamespace(payload=started[0][1], status="failed", result="the render did not pass its checks"))
    detail = await host.call("recipes_list", {"name": "daily-reel"})
    assert "paused" in detail.lower() and "render did not pass" in detail
    assert "Resumed" in await host.call("recipes_manage", {"name": "daily-reel", "action": "resume"})
    assert "paused" not in (await host.call("recipes_list", {"name": "daily-reel"})).split("Recent")[0].lower()


async def test_a_reload_does_not_listen_twice(tmp_path):
    host, _, jobs = build(tmp_path)
    host.reload()
    host.reload()
    assert len(jobs.on_finish) == 1


async def test_pause_resume_delete_and_their_risk(tmp_path):
    host, _, _ = build(tmp_path)
    await create(host)
    tool = host.get_tool("recipes_manage")
    assert tool.risk({"name": "x", "action": "pause"}) == 1 and tool.risk({"name": "x", "action": "delete"}) == 2
    assert "Paused" in await host.call("recipes_manage", {"name": "daily-reel", "action": "pause"})
    assert "Deleted" in await host.call("recipes_manage", {"name": "daily-reel", "action": "delete"})
    assert "No recipe" in await host.call("recipes_manage", {"name": "daily-reel", "action": "pause"})
    assert "pause, resume or delete" in await host.call("recipes_manage", {"name": "x", "action": "explode"})


async def test_the_clock_tick_starts_what_is_due_through_the_service(tmp_path):
    host, started, _ = build(tmp_path)
    await create(host)
    host._services["recipes"].tick(DAY)
    assert len(started) == 1 and started[0][0].startswith("make today's reel")


async def test_a_recipe_can_hold_no_permission_so_no_posting_setting_exists_here(tmp_path):
    host, _, _ = build(tmp_path)
    names = {s.name for s in host.tool_specs() if s.name.startswith("recipes_")}
    assert names == {"recipes_create", "recipes_list", "recipes_run_now", "recipes_manage"}
    schema = host.get_tool("recipes_create").parameters["properties"]
    assert not any(k in schema for k in ("auto", "autopost", "approve", "permission", "allow"))


async def test_without_the_mission_service_it_says_so(tmp_path):
    host = make_host(tmp_path, plugins={"recipes": {"enabled": True}, "missions": {"enabled": False}}, services={})
    await create(host)
    assert "unavailable" in (await host.call("recipes_run_now", {"name": "daily-reel"})).lower()
