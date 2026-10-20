from types import SimpleNamespace

import pytest

from minty_core.commands import CommandRouter


def make():
    agent = SimpleNamespace(tier="fast", force_private=False)
    return CommandRouter(agent), agent


@pytest.mark.parametrize("phrase", [
    "force thinking mode", "Thinking mode", "thinking mode on", "think mode", "deep mode",
    "Minty, force thinking mode!", "hey minty thinking mode", "please deep mode", "Hey Minty, please think mode.",
])
def test_thinking_mode_phrases(phrase):
    router, agent = make()
    assert router.handle(phrase).startswith("Thinking mode on")
    assert agent.tier == "deep"


@pytest.mark.parametrize("phrase", ["normal mode", "Fast mode.", "thinking mode off", "stop thinking mode", "minty normal mode"])
def test_normal_mode_phrases(phrase):
    router, agent = make()
    agent.tier = "deep"
    assert router.handle(phrase) == "Back to normal mode."
    assert agent.tier == "fast"


@pytest.mark.parametrize("phrase", ["private mode", "Private mode on", "hey minty, private mode!"])
def test_private_on_phrases(phrase):
    router, agent = make()
    assert router.handle(phrase).startswith("Private mode on")
    assert agent.force_private is True


def test_private_off_phrase():
    router, agent = make()
    agent.force_private = True
    assert router.handle("Private mode off.") == "Private mode off."
    assert agent.force_private is False


@pytest.mark.parametrize("phrase", ["what mode are you in", "Which mode?"])
def test_status_phrases_describe_both_modes(phrase):
    router, agent = make()
    assert router.handle(phrase) == "I'm in normal mode, and private mode is off."
    agent.tier, agent.force_private = "deep", True
    assert router.handle(phrase) == "I'm in thinking mode, and private mode is on."
    assert (agent.tier, agent.force_private) == ("deep", True)


@pytest.mark.parametrize("phrase", [
    "I'm in normal mode today", "what is thinking mode", "turn on the private pool", "tell me about deep mode",
    "", "   ", "hello", "private mode off the record", "minty",
])
def test_ordinary_sentences_are_not_commands(phrase):
    router, agent = make()
    assert router.handle(phrase) is None
    assert (agent.tier, agent.force_private) == ("fast", False)


@pytest.mark.parametrize("phrase", ["restart", "Reset.", "start over", "Minty, new conversation", "fresh start"])
def test_restart_phrases_reset_the_conversation(phrase):
    from types import SimpleNamespace

    agent = SimpleNamespace(history=[1], tier="fast", force_private=False, reset=0)
    agent.reset_conversation = lambda: setattr(agent, "reset", agent.reset + 1)
    reply = CommandRouter(agent).handle(phrase)
    assert agent.reset == 1 and "saved" in reply
    assert CommandRouter(agent).handle("please restart the washing machine") is None


def test_restart_minty_is_a_pending_action_only_for_the_real_pipeline():
    from types import SimpleNamespace

    agent = SimpleNamespace(history=[], tier="fast", force_private=False, reset_conversation=lambda: 0)
    safe = CommandRouter(agent)  # e.g. the HTTP /ask route: must never restart the process
    assert safe.handle("restart minty") is None and safe.pending_restart is False
    real = CommandRouter(agent, restart=True)
    assert "Restarting" in real.handle("Restart yourself.") and real.pending_restart is True


@pytest.mark.parametrize("phrase", ["shut down", "Minty, shut down.", "stop listening", "go to sleep", "that's all",
                                    "bye", "Nothing. Shut down.", "okay bye", "thanks, that's all", "never mind"])
def test_sleep_phrases_end_the_conversation(phrase):
    agent = SimpleNamespace(history=[], tier="fast", force_private=False)
    router = CommandRouter(agent)
    assert router.handle(phrase) == "Okay." and router.went_to_sleep is True


@pytest.mark.parametrize("phrase", ["don't shut down", "how do I shut down my laptop", "stop the timer",
                                    "play stop by the spice girls", "nothing is working on my computer today"])
def test_ordinary_requests_mentioning_those_words_are_not_dismissals(phrase):
    agent = SimpleNamespace(history=[], tier="fast", force_private=False)
    assert CommandRouter(agent).handle(phrase) is None


@pytest.mark.parametrize("said,mode", [
    ("switch to live mode", "live"), ("Minty, switch to Live mode.", "live"), ("go to live mode", "live"),
    ("can you switch to live mode please", "live"), ("use live mode", "live"), ("turn on live mode", "live"),
    ("turn off classic mode", "live"), ("switch to voice to voice", "live"), ("live mode", "live"),
    ("switch to classic mode", "classic"), ("switch to classic", "classic"), ("go back to classic mode", "classic"),
    ("turn off live mode", "classic"), ("could you change to classic voice", "classic"),
    ("okay use classic", "classic"), ("classic mode", "classic"),
])
def test_voice_mode_phrases(said, mode):
    from minty_core.commands import voice_mode_for
    assert voice_mode_for(said) == mode


@pytest.mark.parametrize("said", ["what is live mode", "classic rock please", "switch to mistral",
                                  "I live in a classic house", "go to sleep"])
def test_other_sentences_are_not_voice_mode_requests(said):
    from minty_core.commands import voice_mode_for
    assert voice_mode_for(said) is None


def test_switch_phrases_for_other_modes_work_too(tmp_path):
    from minty_core.config import ConfigStore
    store = ConfigStore(tmp_path)
    store.load()
    agent = type("A", (), {"tier": "fast", "force_private": False})()
    router = CommandRouter(agent, store)
    assert router.handle("switch to thinking mode").startswith("Thinking mode on") and agent.tier == "deep"
    assert router.handle("turn off thinking mode") == "Back to normal mode." and agent.tier == "fast"
    assert router.handle("go to private mode").startswith("Private mode on") and agent.force_private
    assert router.handle("turn off private mode") == "Private mode off."
    assert router.handle("switch to live mode") == "Live mode on." and router.switch_voice == "live"
    assert store.config.audio.voice_mode == "live"
    assert router.handle("switch to classic mode") == "Classic mode on." and router.switch_voice == "classic"
    assert store.config.audio.voice_mode == "classic"


async def _jobs_router(tmp_path, n_jobs):
    import asyncio
    from minty_core.jobs import JobRunner
    runner = JobRunner(None)

    async def slow(ctx):
        ctx.progress("reading the page")
        await asyncio.sleep(10)

    for i in range(n_jobs):
        runner.submit("browser" if i == 0 else "think", f"goal {i}", slow)
    await asyncio.sleep(0.02)
    agent = type("A", (), {"tier": "fast", "force_private": False})()
    return CommandRouter(agent, jobs=runner), runner


async def test_job_status_and_cancel_by_voice(tmp_path):
    import asyncio
    router, runner = await _jobs_router(tmp_path, 2)
    assert router.handle("What are you doing?").startswith("browser job 1, goal 0")
    assert router.handle("cancel that") == "Stopped deep thinking 2."  # the latest one
    await asyncio.sleep(0.02)
    assert runner.get(2).status == "cancelled" and runner.get(1).status == "running"
    assert router.handle("cancel job 1") == "Stopped browser job 1."
    assert router.handle("cancel job 1") == "There's no running job 1."
    assert router.handle("cancel everything") == "There's nothing running."


@pytest.mark.parametrize("n_jobs, reply, sleeps", [(0, "Okay.", True), (1, "Stopped browser job 1.", False)])
async def test_bare_stop_cancels_work_or_ends_the_conversation(tmp_path, n_jobs, reply, sleeps):
    router, runner = await _jobs_router(tmp_path, n_jobs)
    assert router.handle("stop") == reply and router.went_to_sleep is sleeps
    await runner.cancel_all()


async def test_cancel_all_counts(tmp_path):
    router, runner = await _jobs_router(tmp_path, 3)
    assert router.handle("Stop everything.") == "Stopped 3 jobs."


def test_quick_answers_only_toggle():
    agent = type("A", (), {"tier": "fast", "force_private": False, "quick_only": False})()
    router = CommandRouter(agent)
    assert router.handle("Just the quick answer.") == "Okay, quick answers only." and agent.quick_only
    assert router.handle("you can think more again").startswith("Okay, I'll follow up") and not agent.quick_only
