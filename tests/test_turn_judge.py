"""A small fast model watches the transcript after a pause and decides: has the speaker finished, or should Minty wait."""
import asyncio

import pytest

from minty_core.config import AudioConfig, ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta
from minty_core.voice.turnjudge import JUDGE_SYSTEM, judge_complete, make_turn_judge, needs_judge
from tests.test_pipeline import build, finish, wait_until


# -- when the judge is worth asking ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "so what I was thinking we could do this weekend",  # no full stop, long enough to be a half thought
    "play something relaxing for the evening please",
    "I want to talk about the project that we",
])
def test_an_unpunctuated_thought_is_worth_judging(text):
    assert needs_judge(text)


@pytest.mark.parametrize("text", [
    "What is the capital of France?", "Turn the lights off.", "that is all!",  # the recogniser heard a finished sentence
    "what time is it", "stop", "thanks a lot",  # too short to be half a thought: just answer
    "remind me to call mom and",  # the word rules already say "wait"
    "",
])
def test_everything_else_is_decided_without_a_model(text):
    assert not needs_judge(text)


# -- the verdict ----------------------------------------------------------------------------------------------------

def asking(reply, log=None, delay=0.0):
    async def ask(system, user):
        if log is not None:
            log.append((system, user))
        if delay:
            await asyncio.sleep(delay)
        if isinstance(reply, Exception):
            raise reply
        return reply
    return ask


async def test_the_model_says_finished_or_not():
    assert await judge_complete(asking("COMPLETE"), "tell me a joke about cats", "", 1.0) is True
    assert await judge_complete(asking("incomplete."), "so I was wondering if", "", 1.0) is False
    assert await judge_complete(asking(" Incomplete\n"), "my favourite is the one that", "", 1.0) is False


async def test_a_slow_broken_or_confused_model_never_holds_anything_up():
    assert await judge_complete(asking("COMPLETE", delay=1.0), "tell me a joke about cats", "", 0.1) is None
    assert await judge_complete(asking(RuntimeError("down")), "tell me a joke about cats", "", 1.0) is None
    assert await judge_complete(asking("maybe both?"), "tell me a joke about cats", "", 1.0) is None
    assert await judge_complete(asking(""), "tell me a joke about cats", "", 1.0) is None


async def test_the_model_sees_what_minty_just_asked_so_a_short_answer_counts_as_finished():
    log = []
    await judge_complete(asking("COMPLETE", log), "the blue one I think", "Which colour do you want, red or blue?", 1.0)
    system, user = log[0]
    assert system == JUDGE_SYSTEM and "Which colour do you want" in user and "the blue one I think" in user


# -- wired into the real router: a small free model, never Claude ---------------------------------------------------

async def test_the_judge_asks_a_fast_free_provider_with_no_tools_and_the_small_size(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["providers"] = [{"name": "claude", "kind": "claude_cli", "model": "haiku", "requires_key": False},
                         {"name": "groq", "model": "m", "requires_key": False, "base_url": "http://x"}]
    store.update(data)
    seen = []

    def factory(cfg, key):
        class P:
            name = cfg.name

            async def stream(self, messages, tools):
                seen.append((cfg.name, cfg.model, list(tools), messages[-1].content))
                yield TextDelta("INCOMPLETE")

        return P()

    router = ProviderRouter(store, SecretsStore(tmp_path), factory)
    judge = make_turn_judge(router, store, last_reply=lambda: "", private=lambda: False)
    long_text = "so what I was thinking about doing this weekend with all of my friends from school is going to"
    assert await judge(long_text) is False
    assert [(n, t) for n, _m, t, _c in seen] == [("groq", [])]  # not Claude (a 3 s process), no tools


async def test_a_private_moment_is_never_sent_to_a_model(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    router = ProviderRouter(store, SecretsStore(tmp_path), lambda cfg, key: (_ for _ in ()).throw(AssertionError("called")))
    judge = make_turn_judge(router, store, last_reply=lambda: "", private=lambda: True)
    assert await judge("so what I was thinking about doing this weekend") is None


# -- in the pipeline ------------------------------------------------------------------------------------------------

def _build(tmp_path, texts, verdict):
    p, f = build(tmp_path, stt_texts=texts, replies=[["Done."]])
    data = p.store.config.model_dump(mode="json")
    data["audio"]["smart_turn"] = True
    data["audio"]["turn_hold_ms"] = 1200
    data["audio"]["turn_judge"] = True
    data["audio"]["turn_judge_hold_ms"] = 500
    p.store.update(data)
    calls = []

    async def judge(text):
        calls.append(text)
        return verdict

    p.turn_judge = judge
    return p, f, calls


HALF = "so what I was thinking we should do this weekend"


async def test_the_judge_says_wait_so_the_rest_of_the_thought_is_joined(tmp_path):
    p, f, calls = _build(tmp_path, [HALF, "is go hiking"], verdict=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END", b"SPEECH", b"END", *([b"quiet"] * 20))  # then silence: the judge waits again
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == [f"{HALF} is go hiking"] and calls[0] == HALF and len(f.stt.calls) == 2


async def test_the_judge_says_finished_so_nothing_is_held(tmp_path):
    p, f, calls = _build(tmp_path, [HALF], verdict=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == [HALF] and len(f.stt.calls) == 1


async def test_an_unsure_judge_changes_nothing(tmp_path):
    p, f, _ = _build(tmp_path, [HALF], verdict=None)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == [HALF] and len(f.stt.calls) == 1


async def test_a_finished_sentence_never_reaches_the_judge(tmp_path):
    p, f, calls = _build(tmp_path, ["What should we do this weekend?"], verdict=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert calls == [] and f.agent.heard == ["What should we do this weekend?"]


async def test_nothing_follows_so_the_first_part_goes_on_after_the_hold(tmp_path):
    p, f, calls = _build(tmp_path, [HALF], verdict=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END", *([b"quiet"] * 20))
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == [HALF]


async def test_off_unless_you_turn_it_on(tmp_path):
    assert AudioConfig().turn_judge is False and AudioConfig().turn_judge_hold_ms >= 2000
    p, f, calls = _build(tmp_path, [HALF], verdict=False)
    data = p.store.config.model_dump(mode="json")
    data["audio"]["turn_judge"] = False
    p.store.update(data)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert calls == [] and f.agent.heard == [HALF]
