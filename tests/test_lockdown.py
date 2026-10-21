"""One phrase stops everything and closes the doors until it is explicitly lifted (W1-14)."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeAgent
from tests.safety_helpers import build, call, say
from minty_core.api import create_app
from minty_core.approvals import ApprovalStore
from minty_core.commands import CommandRouter
from minty_core.context import build_context
from minty_core.jobs import JobRunner, make_job_store
from minty_core.lockdown import Lockdown
from minty_core.safety import SafetyKit
from minty_core.secretscan import SecretScanner


def test_the_state_is_persisted_and_only_an_explicit_release_clears_it(tmp_path):
    lock = Lockdown(tmp_path / "lockdown.json")
    assert not lock.active
    assert lock.engage("said the phrase") is True and lock.active
    assert lock.engage("again") is False  # already on
    again = Lockdown(tmp_path / "lockdown.json")  # a restart
    assert again.active and again.reason == "said the phrase"
    assert again.release("terminal") is True and not Lockdown(tmp_path / "lockdown.json").active
    assert again.release("terminal") is False


async def test_it_stops_jobs_and_denies_waiting_approvals_within_two_seconds(tmp_path):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"))
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    waiting = approvals.request("mail_send", {"to": "x"}, "Send it?", risk=3)
    stopped = []

    async def forever(ctx):
        await asyncio.sleep(60)

    jobs = [runner.submit("research", f"job {i}", forever, announce=False) for i in range(5)]
    await asyncio.sleep(0.05)
    lock = Lockdown(tmp_path / "lockdown.json")
    lock.on_engage.append(lambda: [runner.cancel_nowait(j.id) for j in runner.running()])
    lock.on_engage.append(lambda: approvals.deny_all("lockdown"))
    lock.on_engage.append(lambda: stopped.append("remote access revoked"))
    started = time.monotonic()
    lock.engage("test")
    await asyncio.gather(*(runner.wait(j.id) for j in jobs))
    assert time.monotonic() - started < 2.0
    assert all(j.status == "cancelled" for j in jobs) and approvals.get(waiting.id).status == "denied"
    assert stopped == ["remote access revoked"]


def kit(lock):
    def make(store):
        k = SafetyKit(store, SecretScanner())
        k.lockdown = lock
        return k
    return make


async def test_changes_and_network_tools_are_blocked_but_the_clock_still_works(tmp_path):
    lock = Lockdown(tmp_path / "l.json")
    lock.engage("test")
    agent, host, asked = build(tmp_path, {"clock_get_time": (0, None), "files_write": (1, None),
                                          "web_search_search": (0, None)},
                               [[call("clock_get_time", ident="1")], [call("files_write", {"path": "a"}, ident="2")],
                                [call("web_search_search", {"q": "x"}, ident="3")]], safety=kit(lock))
    agent._max_rounds = 5
    await say(agent, "do things")
    assert [n for n, _ in host.called] == ["clock_get_time"]
    results = [m.content for m in agent.history if m.role == "tool"]
    assert sum("lockdown" in r.lower() for r in results) == 2
    lock.release("terminal")
    agent2, host2, _ = build(tmp_path / "b", {"files_write": (1, None)}, [[call("files_write", {"path": "a"})]],
                             safety=kit(lock))
    await say(agent2, "write")
    assert host2.called


def test_by_voice_it_engages_and_only_the_voice_pipeline_can_release(tmp_path):
    lock = Lockdown(tmp_path / "l.json")
    voice = CommandRouter(agent=None, lockdown=lock, allow_unlock=True)
    chat = CommandRouter(agent=None, lockdown=lock)
    assert "lockdown" in chat.handle("lockdown").lower() and lock.active  # engaging is always allowed
    assert chat.handle("end lockdown") is None and lock.active  # a chat message cannot lift it
    assert "lifted" in voice.handle("end lockdown").lower() and not lock.active
    assert voice.handle("end lockdown") == "Lockdown is not on."


def test_the_api_engages_and_releases_with_the_token(tmp_path):
    ctx = build_context(tmp_path)
    ctx.agent = FakeAgent([["ok"]])
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    assert client.post("/lockdown").status_code == 401
    assert client.get("/lockdown", headers=auth).json()["active"] is False
    assert client.post("/lockdown", headers=auth).json()["active"] is True
    assert ctx.services["lockdown"].active
    assert client.post("/lockdown/release", headers=auth).json()["active"] is False


def test_a_state_file_that_cannot_be_read_keeps_lockdown_on_rather_than_silently_lifting_it(tmp_path):
    path = tmp_path / "lockdown.json"
    assert not Lockdown(path).active  # no file: never engaged
    path.write_text("{this is not json")
    broken = Lockdown(path)
    assert broken.active and "unreadable" in broken.reason  # fail closed: something damaged the file
    assert broken.release("terminal") is True and not Lockdown(path).active  # and an explicit release still clears it
    path.write_text('{"active": false, "reason": "", "since": 0}')
    assert not Lockdown(path).active  # a healthy file that says "off" means off
