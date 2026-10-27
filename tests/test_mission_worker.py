"""The real tools a mission gets: the whole safety layer, its own taint record, and the tool set a request needs."""
import pytest

from minty_core.context import build_context


@pytest.fixture
def ctx(tmp_path):
    return build_context(tmp_path)


def names(worker):
    return {s.name for s in worker.specs}


def test_a_mission_never_gets_helpers_installers_other_missions_or_the_terminal(ctx):
    worker = ctx.services["mission_worker"]("check the weather and the time and run a command in the terminal", None)
    assert "clock_get_time" in names(worker) and "need_toolkit" in names(worker)
    assert not any(n.startswith(("missions_", "subagents_", "installer_", "shell_")) for n in names(worker))


def test_it_runs_under_the_conversations_safety_layer_with_its_own_taint_record(ctx):
    helper = ctx.services["mission_worker"]("anything", None).helper
    assert helper.guard is ctx.agent.guard and helper.safety is ctx.agent.safety and helper.receipts is ctx.agent.receipts
    assert helper.provenance is not None and helper.provenance is not ctx.agent.provenance


async def test_a_step_that_needs_a_yes_asks_the_missions_own_confirm_and_a_no_stops_it(ctx):
    asked = []

    async def no(prompt):
        asked.append(prompt)
        return False

    worker = ctx.services["mission_worker"]("delete the old file from the workspace", no)
    assert ctx.host.get_tool("files_delete") is not None
    out = await worker.call("files_delete", {"path": "old.txt"})
    assert out == "The user declined." and len(asked) == 1 and "old.txt" in asked[0]


async def test_asking_for_another_toolkit_adds_its_tools_to_the_same_list(ctx):
    worker = ctx.services["mission_worker"]("hello", None)
    before = names(worker)
    kits = worker.helper.toolkits.kits()
    kit = next(k for k, tools in kits.items() if not set(tools) & before and not any(t.startswith(("missions_", "shell_")) for t in tools))
    listed = worker.specs
    await worker.call("need_toolkit", {"name": kit})
    assert worker.specs is listed and names(worker) > before


async def test_a_private_conversation_makes_the_mission_private(ctx):
    from minty_core.llm.types import Message
    ctx.agent.history.append(Message("tool", "secret", private=True))
    assert ctx.services["mission_worker"]("anything", None).helper.force_private is True


async def test_a_whole_mission_through_the_real_app_with_a_scripted_model(ctx):
    from tests.agent_eval.scripted import ScriptedRouter
    ctx.services["router"] = ScriptedRouter([lambda last: ("tool", "clock_get_time", {}), lambda last: ("say", "It is now noted.")])
    started = await ctx.host.call("missions_start", {"goal": "find out what time it is and tell me"})
    assert started.startswith("Started mission")
    jobs = ctx.services["jobs"]
    job = max(jobs.jobs.values(), key=lambda j: j.id)
    await jobs.wait(job.id)
    assert job.status == "done" and job.checkpoint["verified"] is True
    assert job.checkpoint["ledger"][0]["tool"] == "clock_get_time" and job.checkpoint["ledger"][0]["ok"] is True
    assert "clock_get_time" in await ctx.host.call("missions_report", {"id": job.id})
