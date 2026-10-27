import asyncio
import json
from types import SimpleNamespace

from tests.agent_eval.scripted import ScriptedRouter
from tests.plugin_helpers import make_host
from minty_core.approvals import ApprovalStore
from minty_core.jobs import JobRunner, make_job_store
from minty_core.llm.types import ToolSpec

OBJ = {"type": "object", "properties": {}}


def tool(name, **args):
    return lambda last: ("tool", name, args)


def say(text):
    return lambda last: ("say", text)


class World:
    """The mission's tools, as the app would hand them over: a spec list and a gated call."""

    def __init__(self, answers=None, on_call=None):
        self.answers, self.calls, self.on_call = answers or {}, [], on_call
        self.confirm = None

    def factory(self, goal, confirm):
        self.confirm = confirm
        return SimpleNamespace(specs=[ToolSpec("notes_find", "find notes", OBJ), ToolSpec("mail_send", "send", OBJ)],
                               call=self.call)

    async def call(self, name, args):
        self.calls.append((name, args))
        if self.on_call:
            await self.on_call(name, args, self)
        return self.answers.get(name, "ok")


def build(tmp_path, router, world, approvals=None, **settings):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), approvals=approvals)
    services = {"router": router, "jobs": runner, "mission_worker": world.factory, "is_private": lambda: False}
    host = make_host(tmp_path, plugins={"missions": {"enabled": True, **settings}}, services=services)
    return host, runner


async def start(host, runner, goal="find my notes about the trip and count them"):
    out = await host.call("missions_start", {"goal": goal})
    job = runner.latest_active() or max(runner.jobs.values(), key=lambda j: j.id)
    await runner.wait(job.id)
    return out, runner.get(job.id)


async def test_a_mission_runs_in_the_background_and_reports_what_it_did(tmp_path):
    router = ScriptedRouter([tool("notes_find", q="trip"), say("Found 3 notes about the trip.")])
    host, runner = build(tmp_path, router, World({"notes_find": "3 notes: a, b, c"}))
    out, job = await start(host, runner)
    assert out.startswith("Started mission") and job.kind == "mission"
    assert job.status == "done" and "3 notes" in job.result
    report = await host.call("missions_report", {"id": job.id})
    assert "notes_find" in report and "3 notes: a, b, c" in report and "verified" in report.lower()
    assert "not instructions" in report  # tool output inside is somebody's data


async def test_a_mission_nobody_could_double_check_says_so(tmp_path):
    router = ScriptedRouter([tool("notes_find", q="trip"), say("Found 3 notes.")], verifier=lambda user: None)
    host, runner = build(tmp_path, router, World())
    _, job = await start(host, runner)
    assert job.status == "done" and "could not double-check" in job.result
    assert "not double-checked" in (await host.call("missions_report", {"id": job.id})).lower()


async def test_a_mission_that_only_talks_does_not_finish(tmp_path):
    router = ScriptedRouter([say("All done, I emailed everyone!")],
                            verifier=lambda user: "No tool calls were made" not in user)
    host, runner = build(tmp_path, router, World())
    _, job = await start(host, runner, goal="email everyone on the list")
    assert job.status == "blocked" and job.question


async def test_a_blocked_mission_carries_on_when_the_user_answers(tmp_path):
    router = ScriptedRouter([say("I booked it.")])
    router.verifier = lambda user: any("Friday" in x for x in router.executor_inputs)
    host, runner = build(tmp_path, router, World())
    _, job = await start(host, runner, goal="book the dentist")
    assert job.status == "blocked" and job.question
    assert (await host.call("missions_answer", {"id": job.id, "answer": "Friday"})).startswith("Carrying on")
    await runner.wait(job.id)
    assert runner.get(job.id).status == "done" and runner.get(job.id).resumed == 1
    assert "Friday" in router.executor_inputs[-1]


async def test_only_a_blocked_mission_takes_an_answer(tmp_path):
    router = ScriptedRouter([tool("notes_find", q="a"), say("done")])
    host, runner = build(tmp_path, router, World())
    _, job = await start(host, runner)
    assert "isn't waiting" in await host.call("missions_answer", {"id": job.id, "answer": "x"})
    assert "No mission" in await host.call("missions_answer", {"id": 999, "answer": "x"})


async def test_a_mission_cut_off_by_a_restart_can_be_resumed_from_its_saved_plan(tmp_path):
    cut = {"first": True}

    async def cut_off(name, args, world):
        if cut["first"]:
            cut["first"] = False
            raise asyncio.CancelledError  # the program stops here

    router = ScriptedRouter([tool("notes_find", q="a"), say("Found it.")])
    host, runner = build(tmp_path, router, World(on_call=cut_off))
    await host.call("missions_start", {"goal": "find my notes about the trip and count them"})
    job = max(runner.jobs.values(), key=lambda j: j.id)
    await runner.wait(job.id)
    assert runner.get(job.id).status == "interrupted"
    assert json.loads(runner.get(job.id).checkpoint["plan"])["goal"]

    host2, runner2 = build(tmp_path, ScriptedRouter([tool("notes_find", q="a"), say("Found it.")]), World())
    assert [j.id for j in runner2.resumable()] == [job.id]
    assert "Carrying on" in await host2.call("missions_resume", {"id": job.id})
    await runner2.wait(job.id)
    assert runner2.get(job.id).status == "done"


async def test_a_risky_step_waits_for_the_users_yes_and_the_mission_carries_on(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    seen = {}

    async def risky(name, args, world):
        if name == "mail_send":
            seen["answer"] = await world.confirm("Send the update to Sam?")

    router = ScriptedRouter([tool("mail_send", to="sam"), say("Sent the update to Sam.")])
    host, runner = build(tmp_path, router, World(on_call=risky), approvals=approvals)
    await host.call("missions_start", {"goal": "tell Sam the plan changed"})
    job = max(runner.jobs.values(), key=lambda j: j.id)
    for _ in range(100):
        if approvals.pending():
            break
        await asyncio.sleep(0.02)
    assert runner.get(job.id).status == "waiting_approval" and "Sam" in approvals.pending()[0].question
    approvals.decide(approvals.pending()[0].id, True, via="test")
    await runner.wait(job.id)
    assert seen["answer"] is True and runner.get(job.id).status == "done"


async def test_a_risky_step_the_user_never_answers_is_a_no(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    seen = {}

    async def risky(name, args, world):
        seen["answer"] = await world.confirm("Delete the old notes?")

    router = ScriptedRouter([tool("mail_send", to="x"), say("done")])
    host, runner = build(tmp_path, router, World(on_call=risky), approvals=approvals)
    await host.call("missions_start", {"goal": "clean up"})
    job = max(runner.jobs.values(), key=lambda j: j.id)
    for _ in range(100):
        if approvals.pending():
            break
        await asyncio.sleep(0.02)
    approvals.decide(approvals.pending()[0].id, False, via="test")
    await runner.wait(job.id)
    assert seen["answer"] is False


async def test_start_is_checked_and_capped(tmp_path):
    router = ScriptedRouter([tool("notes_find", q="a"), say("done")])

    async def slow(name, args, world):
        await asyncio.sleep(5)

    host, runner = build(tmp_path, router, World(on_call=slow), max_concurrent=1)
    assert "Tell me" in await host.call("missions_start", {"goal": "  "})
    assert "too long" in await host.call("missions_start", {"goal": "x" * 3000})
    assert (await host.call("missions_start", {"goal": "find my notes"})).startswith("Started")
    assert "already running" in await host.call("missions_start", {"goal": "another one"})
    await runner.cancel_all()


async def test_without_the_app_services_it_says_so_instead_of_pretending(tmp_path):
    host = make_host(tmp_path, plugins={"missions": {"enabled": True}}, services={})
    assert "unavailable" in (await host.call("missions_start", {"goal": "do a thing"})).lower()
    assert "unavailable" in (await host.call("missions_report", {})).lower()


async def test_the_tools_are_ask_first_where_they_start_work(tmp_path):
    host, _ = build(tmp_path, ScriptedRouter([say("x")]), World())
    assert host.get_tool("missions_start").risk == 1 and host.get_tool("missions_report").risk == 0


async def test_the_review_reads_the_record_and_proposes_without_changing_anything(tmp_path):
    router = ScriptedRouter([tool("notes_find", q="a"), say("done")])
    host, runner = build(tmp_path, router, World())
    host._services["coding_jobs"] = lambda: [
        {"id": i, "project": "web", "agent": "claude_code", "status": "done", "verdict": "unchecked", "rounds": 1}
        for i in range(3)]
    out = await host.call("missions_review", {})
    assert "web" in out and "check_command" in out and "Nothing was changed" in out
    assert host.get_tool("missions_review").risk == 0
    assert "Not enough" in await build(tmp_path / "fresh", router, World())[0].call("missions_review", {})


async def test_a_resumed_mission_gives_its_gate_back_what_it_had_read(tmp_path):
    seen = []

    class Prov:
        def note_result(self, tool, result):
            seen.append(tool)

    class Resumed(World):
        def factory(self, goal, confirm):
            worker = super().factory(goal, confirm)
            worker.helper = SimpleNamespace(provenance=Prov())
            return worker

    cut = {"first": True}

    async def cut_off(name, args, world):
        if cut["first"] and name == "notes_find":
            cut["first"] = False
            raise asyncio.CancelledError

    router = ScriptedRouter([tool("mail_send", to="x"), tool("notes_find", q="a"), say("done")])
    host, runner = build(tmp_path, router, Resumed(on_call=cut_off))
    await host.call("missions_start", {"goal": "tell sam, then look for my notes"})
    job = max(runner.jobs.values(), key=lambda j: j.id)
    await runner.wait(job.id)
    assert runner.get(job.id).status == "interrupted" and runner.get(job.id).checkpoint["ledger"]
    seen.clear()
    host2, runner2 = build(tmp_path, ScriptedRouter([say("done")]), Resumed())
    await host2.call("missions_resume", {"id": job.id})
    await runner2.wait(job.id)
    assert "mail_send" in seen  # what it did before the restart is known again to the gate


async def test_the_review_includes_the_offers_to_trust_someone_more(tmp_path):
    from minty_core.worklessons import Lesson
    host, _ = build(tmp_path, ScriptedRouter([say("x")]), World())
    host._services["outbox_lessons"] = lambda: [Lesson("outbox:mum@home.in:autoreply", "You said yes to 6 replies to mum@home.in",
                                                      "add it to auto_reply_to yourself", "confirmed", 6)]
    out = await host.call("missions_review", {})
    assert "mum@home.in" in out and "auto_reply_to" in out and "Nothing was changed" in out
