"""Offline mode: local answers, an honest refusal, and no waiting on dead providers (W1-25)."""
from datetime import datetime

from tests.safety_helpers import build, call, say
from minty_core.commands import CommandRouter
from minty_core.llm.types import LLMError, TextDelta
from minty_core.offline import CAN_STILL, NetworkProbe, OfflineHandler

NOW = datetime(2026, 10, 14, 17, 42)


class Calls:
    def __init__(self):
        self.log = []

    async def __call__(self, name, args):
        self.log.append((name, args))
        return {"timers_set_timer": "Timer 1 set: timer, 600 seconds.", "reminders_set": "Okay, reminder set.",
                "journal_add_entry": "Saved.", "timers_list_timers": "No timers running.",
                "reminders_list": "No reminders."}[name]


def handler(online=True):
    async def probe():
        return online
    calls = Calls()
    return OfflineHandler(calls, NetworkProbe(probe), clock=lambda: NOW), calls


async def test_six_scripted_scenarios_work_without_a_model():
    h, calls = handler(online=False)
    assert await h.handle("what time is it?") == "It's 5:42 p m."
    assert await h.handle("what's the date") == "It's Wednesday, 14 October."
    assert await h.handle("set a timer for 10 minutes") == "Timer 1 set: timer, 600 seconds."
    assert calls.log[-1] == ("timers_set_timer", {"seconds": 600.0})
    assert await h.handle("remind me to call the dentist at 6 pm") == "Okay, reminder set."
    assert calls.log[-1] == ("reminders_set", {"text": "call the dentist", "at": "18:00"})
    assert await h.handle("remind me to stretch in half an hour") == "Okay, reminder set."
    assert calls.log[-1] == ("reminders_set", {"text": "stretch", "in_minutes": 30.0})
    assert await h.handle("take a note: buy a notebook for chemistry") == "Saved."
    assert await h.handle("what are my timers") == "No timers running. No reminders."


async def test_anything_else_is_an_honest_no_that_names_what_still_works():
    h, _ = handler(online=False)
    out = await h.answer("write me an essay on photosynthesis")
    assert out == f"I'm offline right now, so I can't do that. I can still {CAN_STILL}."
    assert await h.handle("what's the capital of France") is None


async def test_the_probe_is_cached_and_can_be_forced():
    seen = []

    async def probe():
        seen.append(1)
        return False

    p = NetworkProbe(probe, ttl_s=15, clock=lambda: 100.0)
    assert not await p.online() and not await p.online() and len(seen) == 1
    assert p.known_offline()
    p.note_failure()
    assert not p.known_offline()
    p.forced = True
    assert p.known_offline() and not await p.online()
    p.forced = False
    assert await p.online()


async def test_when_known_offline_the_agent_answers_locally_and_never_calls_a_model(tmp_path):
    h, calls = handler(online=False)
    await h.probe.online()  # the probe said offline
    agent, host, _ = build(tmp_path, {}, [])
    agent.offline = h
    called = []

    async def stream(messages, tools, **kw):
        called.append(1)
        yield TextDelta("model")

    agent._router.stream = stream
    assert await say(agent, "set a timer for 10 minutes") == ["Timer 1 set: timer, 600 seconds."]
    assert await say(agent, "explain gravity") == [f"I'm offline right now, so I can't do that. I can still {CAN_STILL}."]
    assert called == []
    assert [m.role for m in agent.history][-2:] == ["user", "assistant"]


async def test_a_failed_model_call_with_the_network_down_gets_the_local_answer(tmp_path):
    h, _ = handler(online=False)
    agent, host, _ = build(tmp_path, {}, [])
    agent.offline = h

    async def stream(messages, tools, **kw):
        raise LLMError("all providers failed", kind="timeout")
        yield  # noqa

    agent._router.stream = stream
    out = await agent.offline_reply("what time is it")
    assert out == "It's 5:42 p m."
    h2, _ = handler(online=True)
    agent.offline = h2
    assert await agent.offline_reply("what time is it") is None  # the network is up: the models are the problem


def test_by_voice_it_can_be_forced(tmp_path):
    h, _ = handler(online=True)
    router = CommandRouter(agent=None, offline=h.probe)
    assert "offline" in router.handle("go offline").lower() and h.probe.forced is True
    assert "online" in router.handle("go online").lower() and h.probe.forced is None
