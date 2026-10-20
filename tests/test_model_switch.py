"""Picking the model by voice, and showing which one is answering."""
import pytest

from tests.test_router import collect, make_router, P
from minty_core.commands import CommandRouter, spoken_model
from minty_core.llm.types import TextDelta
from minty_core.tui import status_items

PROVIDERS = [P("groq", model="openai/gpt-oss-120b"), P("gemini", model="gemini-3.8-flash"),
             P("nvidia", model="nvidia/nemotron-3-super"), P("nvidia-deep", model="moonshotai/kimi-k3", tier="deep"),
             P("nvidia-gemma", model="google/gemma-4-31b-it"), P("nokey", model="x")]
KEYS = ["groq", "gemini", "nvidia", "nvidia-deep", "nvidia-gemma"]


def router_for(tmp_path):
    scripts = {p["name"]: [TextDelta(p["name"])] for p in PROVIDERS}
    return make_router(tmp_path, PROVIDERS, KEYS, scripts)


class FakeAgent:
    tier = "fast"
    force_private = False


@pytest.mark.parametrize("spoken,name", [
    ("gemini", "gemini"), ("kimi", "nvidia-deep"), ("kimmy", "nvidia-deep"), ("gpt oss", "groq"),
    ("grok", "groq"), ("nvidia deep", "nvidia-deep"), ("nvidia", "nvidia"), ("gemma", "nvidia-gemma"),
    ("the gemini model", "gemini"), ("gemini 3.8 flash", "gemini"),
])
def test_spoken_names_find_the_provider(tmp_path, spoken, name):
    router, _ = router_for(tmp_path)
    assert router.find(spoken).name == name


@pytest.mark.parametrize("spoken", ["nokey", "claude", "it", "the browser to play music", ""])
def test_unknown_or_unusable_names_find_nothing(tmp_path, spoken):
    router, _ = router_for(tmp_path)
    assert router.find(spoken) is None


async def test_the_users_pick_answers_first_and_is_remembered_as_active(tmp_path):
    router, calls = router_for(tmp_path)
    commands = CommandRouter(FakeAgent(), router._store, router=router)
    assert commands.handle("Minty, switch to Kimi.").startswith("Switched to nvidia-deep, kimi k3.")
    assert router._store.config.preferred_provider == "nvidia-deep"
    events = await collect(router)
    assert events == [TextDelta("nvidia-deep")] and router.active == ("nvidia-deep", "moonshotai/kimi-k3")
    assert commands.handle("switch back to automatic") == "Okay, I'll pick the model automatically again."
    calls.clear()
    await collect(router)
    assert calls == ["groq"]


async def test_an_explicit_prefer_beats_the_users_pick(tmp_path):
    router, calls = router_for(tmp_path)
    data = router._store.config.model_dump(mode="json")
    data["preferred_provider"] = "groq"
    router._store.update(data)
    [e async for e in router.stream([], [], prefer="gemini")]
    assert calls == ["gemini"]


@pytest.mark.parametrize("said", ["Can you switch to Mistral?", "okay switch the model to mistral",
                                  "I want you to use mistral", "let's go with mistral please"])
def test_polite_and_casual_switches(tmp_path, said):
    router, _ = router_for(tmp_path)
    data = router._store.config.model_dump(mode="json")
    data["providers"].append({"name": "mistral", "model": "mistral-small-latest"})
    router._store.update(data)
    router._secrets.set("mistral", "k")
    reply = CommandRouter(FakeAgent(), router._store, router=router).handle(said)
    assert reply.startswith("Switched to mistral") and router._store.config.preferred_provider == "mistral"


def test_which_model_lists_the_choices(tmp_path):
    router, _ = router_for(tmp_path)
    reply = CommandRouter(FakeAgent(), router._store, router=router).handle("which model are you using")
    assert "choosing automatically" in reply and "gemini" in reply and "nokey" not in reply


def test_unknown_model_lists_choices_but_other_switches_fall_through(tmp_path):
    router, _ = router_for(tmp_path)
    commands = CommandRouter(FakeAgent(), router._store, router=router)
    assert commands.handle("switch the model to claude").startswith("I don't have a model called claude")
    assert commands.handle("use the browser to play lofi") is None
    assert commands.handle("switch to dark mode") is None
    assert router._store.config.preferred_provider == ""


def test_spoken_model_names():
    assert spoken_model("moonshotai/kimi-k3") == "kimi k3"
    assert spoken_model("nvidia/nemotron-3-super-120b-a12b:free") == "nemotron 3 super 120b a12b"


def test_terminal_shows_the_model():
    st = {"model": "gemini · gemini-3.8-flash", "picked": "gemini", "models": ["groq", "gemini"]}
    items = dict((label, value) for label, value, _ in status_items(st))
    assert items["MODEL"] == "gemini · gemini-3.8-flash ★"  # ★ marks your pick
    items = dict((label, value) for label, value, _ in status_items({"voice_mode": "live"}))
    assert items["MODEL"] == "Gemini Live"
    items = dict((label, value) for label, value, _ in status_items({"model": "groq · x", "picked": "gemini"}))
    assert items["MODEL"].endswith("(gemini unavailable)")
