"""Playbooks in use: created only from the user's words, offered to a matching mission, and judged by how its runs end."""
from minty_core.policy import Guard
from tests.plugin_helpers import make_host
from tests.test_policy_mandate import Store

STEPS = "Look in artifacts_list first; one row per item with source and date; rank with a reason; read it back."
NEW = {"name": "market-research", "triggers": "research competitors market compare rank", "steps": STEPS}


async def test_the_tools_create_list_propose_accept_roll_back_and_remove(tmp_path):
    host = make_host(tmp_path)
    assert "No playbooks" in await host.call("playbooks_list", {})
    assert "Saved" in await host.call("playbooks_create", NEW)
    assert "market-research" in await host.call("playbooks_list", {})
    assert "Nothing changes until" in await host.call("playbooks_propose", {"name": "market-research", "steps": STEPS + " Add founders."})
    assert "Accepted" in await host.call("playbooks_accept", {"name": "market-research", "version": 2})
    assert "Went back" in await host.call("playbooks_rollback", {"name": "market-research"})
    assert "Removed" in await host.call("playbooks_remove", {"name": "market-research"})


async def test_only_the_users_words_create_a_playbook_and_accepting_one_is_asked(tmp_path):
    host = make_host(tmp_path)
    create, accept = host.get_tool("playbooks_create"), host.get_tool("playbooks_accept")

    async def verdict(tool, name, args, request):
        asked = []

        async def confirm(q):
            asked.append(q)
            return False

        d = await Guard(Store()).authorize(tool, name, args, confirm, request=request)
        return d.allowed and not asked, asked

    assert (await verdict(create, "playbooks_create", NEW, "save that as a playbook for market research"))[0]
    ok, asked = await verdict(create, "playbooks_create", NEW, "what is the weather")
    assert not ok and asked
    ok, asked = await verdict(accept, "playbooks_accept", {"name": "market-research", "version": 2}, "research competitors")
    assert not ok and asked and "from now on" in asked[0]


async def test_a_mission_is_offered_the_matching_playbook_and_its_end_is_recorded(tmp_path, monkeypatch):
    from minty_core.missions import MissionResult
    from tests.test_plugin_missions import World, build, start

    seen = {}

    async def fake_run(router, goal, **kw):
        seen["context"] = kw["context"]
        return MissionResult("done", "ok", verified=True)

    monkeypatch.setattr("minty_core.missions.run_mission", fake_run)  # before the plugin is loaded: it imports the name when it loads
    host, runner = build(tmp_path, object(), World())
    await host.call("playbooks_create", NEW)
    await start(host, runner, "research the competitors in the dental market and rank them")
    assert "market-research" in seen["context"] and "Steps:" in seen["context"] and "Adapt it" in seen["context"]
    [s] = host._services["playbooks"].stats("market-research")
    assert s["runs"] == 1 and s["done"] == 1
    seen.clear()
    await start(host, runner, "what is on my calendar")  # no playbook for this: nothing offered, nothing recorded
    assert "market-research" not in seen.get("context", "")
    assert host._services["playbooks"].stats("market-research")[0]["runs"] == 1
