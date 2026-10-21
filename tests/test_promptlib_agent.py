"""The prompt library inside the agent: channel, tier and tool group choose the modules; gates and switches rule."""
import pytest

from minty_core import promptlib
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import TextDelta
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from tests.test_agent import FakeHost, ScriptedRouter


@pytest.fixture
def gates():
    saved = dict(promptlib.GATES)
    promptlib.GATES.clear()
    yield promptlib.GATES
    promptlib.GATES.clear()
    promptlib.GATES.update(saved)


def make(tmp_path, tier="fast", off=(), library=True):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["personality"]["prompt_library"] = library
    data["personality"]["prompt_modules_off"] = list(off)
    store.update(data)
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    router = ScriptedRouter([[TextDelta("ok.")], [TextDelta("ok.")]])
    return Agent(router, FakeHost(), store, personalities, MemoryStore(tmp_path / "m.md"), confirm, tier=tier), router


async def ask(agent, text, **kw):
    return [s async for s in agent.respond(text, **kw)]


async def test_nothing_changes_until_a_module_has_passed_its_gate(tmp_path, gates):
    agent, router = make(tmp_path)
    await ask(agent, "hello")
    system = router.calls[0][0].content
    assert promptlib.MODULES[0].text not in system and "Telegram" not in system


async def test_a_gated_module_is_added_and_a_switch_takes_it_away(tmp_path, gates):
    gates["wellbeing"] = "passed"
    agent, router = make(tmp_path)
    await ask(agent, "hello")
    assert promptlib.MODULES[0].text in router.calls[0][0].content
    agent2, router2 = make(tmp_path / "b", off=["wellbeing"])
    await ask(agent2, "hello")
    assert promptlib.MODULES[0].text not in router2.calls[0][0].content
    agent3, router3 = make(tmp_path / "c", library=False)
    await ask(agent3, "hello")
    assert promptlib.MODULES[0].text not in router3.calls[0][0].content


async def test_a_deep_tier_module_is_used_only_in_thinking_mode(tmp_path, gates, monkeypatch):
    deep_only = promptlib.Module("deep_only", 1, "DEEP ONLY RULE.", 40, tiers=("deep",))
    monkeypatch.setattr(promptlib, "MODULES", (*promptlib.MODULES, deep_only))
    gates["deep_only"] = "w16-test"
    fast, rf = make(tmp_path / "f")
    deep, rd = make(tmp_path / "d", tier="deep")
    await ask(fast, "hi")
    await ask(deep, "hi")
    assert "DEEP ONLY RULE." not in rf.calls[0][0].content and "DEEP ONLY RULE." in rd.calls[0][0].content


async def test_a_telegram_turn_gets_the_text_rules_instead_of_the_spoken_ones(tmp_path, gates):
    gates["telegram_rules"] = "passed"
    from minty_core.personality import BASE_RULES

    agent, router = make(tmp_path)
    await ask(agent, "hello", chat=True)
    await ask(agent, "hello again")
    chat, voice = router.calls[0][0].content, router.calls[1][0].content
    assert promptlib.TELEGRAM_RULES in chat and BASE_RULES not in chat
    assert BASE_RULES in voice and promptlib.TELEGRAM_RULES not in voice


async def test_the_prompt_version_is_in_the_trace(tmp_path, gates):
    from minty_core import trace

    gates["wellbeing"] = "passed"
    agent, _ = make(tmp_path)
    seen = {}
    real = trace.annotate_turn

    def spy(**fields):
        seen.update(fields)
        real(**fields)

    import minty_core.agent as agent_module
    agent_module.annotate_turn = spy
    try:
        await ask(agent, "hello")
    finally:
        agent_module.annotate_turn = real
    assert seen.get("prompt_modules") == ["wellbeing"] and len(seen.get("prompt_version", "")) == 10


def test_prompts_show_prints_what_would_be_sent_with_sizes(tmp_path, gates, capsys, monkeypatch):
    from minty_core.app import main

    gates.update({"wellbeing": "w16-x", "tutor": "w16-x"})
    monkeypatch.setenv("MINTY_CONFIG_DIR", str(tmp_path))
    main(["prompts-show", "--tier", "deep", "--kit", "study"])
    out = capsys.readouterr().out
    assert "channel voice, tier deep" in out and "wellbeing v1" in out and "tutor v2" in out
    assert "characters" in out and "version " in out
    main(["prompts-show", "--channel", "telegram"])
    assert "channel telegram, tier fast" in capsys.readouterr().out


def test_the_built_in_prompt_modules_are_neutral_about_who_and_where_the_user_is():
    """A shipped prompt carries no one's age, country or phone number: the user's own details come from their profile and persona."""
    from minty_core import promptlib

    text = " ".join(m.text for m in promptlib.MODULES).lower()
    for word in ("teen", "tele-manas", "14416", "india", "school", "child"):
        assert word not in text, word
    assert "crisis line" in text  # a crisis is still met with care and a pointer to real help
