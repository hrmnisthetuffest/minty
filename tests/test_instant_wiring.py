"""Instant answers inside the assistant: the agent answers before any model call, and says so in the trace."""
import random
from pathlib import Path

from minty_core.agent import Agent
from minty_core.config import Config, ConfigStore
from minty_core.instant.wiring import attach, with_actions
from minty_core.llm.types import TextDelta
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool
from tests.instant_fakes import NOW

ROOT = Path(__file__).resolve().parents[1]


class Router:
    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), 0

    async def stream(self, messages, tools, *, sensitive=False, tier="fast", **kw):
        self.calls += 1
        for ev in self.rounds.pop(0):
            yield ev


class Host:
    def __init__(self, result="Timer 1 (timer): 125 seconds left."):
        self.called, self.result = [], result
        self.tools = {n: Tool(n, n, {}, lambda a: "") for n in ("timers_set_timer", "timers_list_timers", "system_control")}

    def tool_specs(self):
        return []

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append((name, args))
        return self.result


class Tracer:
    def __init__(self):
        self.events = []

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def build(tmp_path, *rounds, host=None, music=None):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    router, host = Router(*rounds), host or Host()
    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    agent.tracer = Tracer()
    services = {"music_watcher": music} if music else {}
    instant = attach(agent, store, services, clock=lambda: NOW, rng=random.Random(3))
    agent.fastpath = with_actions(lambda text: None, store)
    return agent, router, host, store, instant


async def ask(agent, text, **kw):
    return [s async for s in agent.respond(text, **kw)]


async def test_a_simple_question_is_answered_with_no_model_call(tmp_path):
    agent, router, host, *_ = build(tmp_path)
    assert await ask(agent, "Minty, what time is it?") == ["It's 4:32 PM."]
    assert router.calls == 0 and host.called == []
    assert [m.role for m in agent.history] == ["user", "assistant"] and agent.history[-1].content == "It's 4:32 PM."
    assert agent.tracer.events == [("instant", {"rule": "time now", "category": "time and date"})]


async def test_turn_hooks_see_an_instant_answer_like_any_other(tmp_path):
    agent, *_ = build(tmp_path)
    seen = []
    agent.turn_done.append(lambda request, reply: seen.append((request, reply)))
    await ask(agent, "what is 12 times 8")
    assert seen == [("what is 12 times 8", "12 times 8 is 96.")]


async def test_anything_else_goes_to_the_model_as_before(tmp_path):
    agent, router, *_ = build(tmp_path, [TextDelta("Because of Rayleigh scattering.")])
    assert await ask(agent, "why is the sky blue") == ["Because of Rayleigh scattering."]
    assert router.calls == 1 and agent.tracer.events == []


async def test_text_that_came_from_outside_is_never_answered_locally(tmp_path):
    agent, router, *_ = build(tmp_path, [TextDelta("ok")])
    await ask(agent, "what time is it", untrusted="forwarded message")
    assert router.calls == 1


async def test_it_can_be_switched_off_and_categories_left_to_the_model(tmp_path):
    agent, router, _, store, _ = build(tmp_path, [TextDelta("model")], [TextDelta("model again")])
    data = store.config.model_dump(mode="json")
    data["instant"]["skip"] = ["time and date"]
    store.update(data)
    assert await ask(agent, "what time is it") == ["model"]
    assert await ask(agent, "what is 2 plus 2") == ["2 plus 2 is 4."]  # other categories still instant
    data["instant"] = {"enabled": False, "skip": [], "actions": True}
    store.update(data)
    agent2, router2, *_ = build(tmp_path, [TextDelta("model")])
    store2 = ConfigStore(tmp_path)
    store2.load()
    assert store2.config.instant.enabled is False
    assert await ask(agent2, "what is 2 plus 2") == ["model"] and router2.calls == 1


async def test_say_that_again_repeats_the_last_answer_and_what_did_i_ask_remembers(tmp_path):
    agent, router, *_ = build(tmp_path, [TextDelta("The capital of Peru is Lima, a city on the coast.")])
    await ask(agent, "tell me about peru")
    assert await ask(agent, "say that again") == ["The capital of Peru is Lima, a city on the coast."]
    assert await ask(agent, "what did I just ask") == ["You asked: say that again"]
    assert router.calls == 1


async def test_say_that_again_with_nothing_said_yet(tmp_path):
    agent, *_ = build(tmp_path)
    assert await ask(agent, "say that again") == ["I haven't said anything yet."]


async def test_what_is_playing_reads_the_music_watcher(tmp_path):
    class Watcher:
        def now_playing(self):
            return {"title": "Starboy", "artist": "The Weeknd", "status": "Playing"}

    agent, router, *_ = build(tmp_path, music=Watcher())
    assert await ask(agent, "what song is this") == ["“Starboy” by The Weeknd."]
    assert router.calls == 0


async def test_without_a_music_watcher_the_question_goes_on(tmp_path):
    agent, router, *_ = build(tmp_path, [TextDelta("I can't tell.")])
    await ask(agent, "what song is this")
    assert router.calls == 1


async def test_a_timer_is_one_tool_call_and_no_model(tmp_path):
    agent, router, host, *_ = build(tmp_path)
    assert await ask(agent, "set a timer for 5 minutes") == ["Timer set for 5 minutes."]
    assert host.called == [("timers_set_timer", {"seconds": 300, "label": ""})] and router.calls == 0


async def test_the_timer_status_is_read_from_the_tool_result(tmp_path):
    agent, router, host, *_ = build(tmp_path)
    assert await ask(agent, "how long is left on the timer") == ["2 minutes 5 seconds left on the timer."]
    assert host.called == [("timers_list_timers", {})]


async def test_a_failing_tool_falls_back_to_the_model(tmp_path):
    agent, router, *_ = build(tmp_path, [TextDelta("Spotify isn't connected.")], host=Host(result="Error: Spotify is not logged in."))
    agent.fastpath = with_actions(lambda text: None, agent._store)
    assert await ask(agent, "set a timer for 5 minutes") == ["Spotify isn't connected."]
    assert router.calls == 1


async def test_actions_can_be_switched_off(tmp_path):
    agent, router, host, store, _ = build(tmp_path, [TextDelta("Okay.")])
    data = store.config.model_dump(mode="json")
    data["instant"]["actions"] = False
    store.update(data)
    await ask(agent, "set a timer for 5 minutes")
    assert host.called == [] and router.calls == 1


async def test_the_page_commands_still_win_over_the_new_ones(tmp_path):
    from minty_core.fastpath import FastAction

    agent, router, host, store, _ = build(tmp_path)
    agent.fastpath = with_actions(lambda text: FastAction("system_control", {"action": "lock"}, "Locked it.") if text == "lock" else None, store)
    assert await ask(agent, "lock") == ["Locked it."]


def test_the_settings_exist_and_default_to_on():
    cfg = Config()
    assert cfg.instant.enabled is True and cfg.instant.actions is True and cfg.instant.skip == []


def test_the_command_line_answers_and_lists(capsys):
    from minty_core.instant.cli import run

    assert run(["what", "is", "12", "times", "8"], clock=lambda: NOW) == 0
    assert capsys.readouterr().out.strip() == "12 times 8 is 96."
    assert run(["why", "is", "the", "sky", "blue"], clock=lambda: NOW) == 1
    assert "model" in capsys.readouterr().out
    assert run([], clock=lambda: NOW) == 0
    out = capsys.readouterr().out
    assert "time and date" in out and "what time is it" in out and "maths" in out


def test_the_catalog_document_is_up_to_date():
    from minty_core.instant.cli import catalog_markdown

    assert (ROOT / "docs" / "instant-answers.md").read_text() == catalog_markdown(), (
        "run: PYTHONPATH=src python -m minty_core.instant.cli --write")
