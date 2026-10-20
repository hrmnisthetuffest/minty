"""Privacy mode: nothing leaves the machine except to approved providers, with a visible banner (W1-19)."""
from types import SimpleNamespace

from tests.safety_helpers import build, call, say
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore
from minty_core.safety import SafetyKit
from minty_core.secretscan import SecretScanner
from minty_core.statusline import banner


def kit(store):
    return SafetyKit(store, SecretScanner())


async def test_network_tools_are_refused_and_local_ones_still_work(tmp_path):
    agent, host, _ = build(
        tmp_path, {"clock_get_time": (0, None), "web_search_search": (0, None), "files_download": (1, None),
                   "browser_open": (0, None), "memory_recall": (0, None), "smart_home_set_device": (1, None)},
        [[call("clock_get_time", ident="1")], [call("web_search_search", {"q": "x"}, ident="2")],
         [call("files_download", {"url": "https://x.example/a"}, ident="3")], [call("browser_open", ident="4")],
         [call("memory_recall", {"query": "x"}, ident="5")], [call("smart_home_set_device", ident="6")]],
        settings={"safety": {"privacy_mode": True}}, safety=kit)
    agent._max_rounds = 8
    await say(agent, "do things")
    assert [n for n, _ in host.called] == ["clock_get_time", "memory_recall", "smart_home_set_device"]
    blocked = [m.content for m in agent.history if m.role == "tool" and m.content.startswith("Blocked")]
    assert len(blocked) == 3 and all("privacy mode is on" in b for b in blocked)


async def test_every_request_is_routed_privately(tmp_path):
    seen = []
    agent, host, _ = build(tmp_path, {}, [[]], settings={"safety": {"privacy_mode": True}})

    class Spy:
        async def stream(self, messages, tools, **kw):
            seen.append(kw.get("sensitive"))
            from minty_core.llm.types import TextDelta
            yield TextDelta("ok")

    agent._router = Spy()
    await say(agent, "hello")
    assert seen == [True]


def test_by_voice_and_the_banner(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    router = CommandRouter(agent=SimpleNamespace(force_private=False), store=store)
    assert banner(store.config) == ""
    assert "Privacy mode on" in router.handle("privacy mode on")
    assert store.config.safety.privacy_mode is True
    assert banner(store.config) == "PRIVACY MODE: nothing leaves this computer except to providers that don't train on it"
    assert "Privacy mode off" in router.handle("privacy mode off")
    assert store.config.safety.privacy_mode is False
