"""Minty can tell the user something on their own Telegram when they ask ("send me that on Telegram"): one message to the paired chat,
never to anyone else. Before this there was no tool for it, so the model truthfully said it had no access."""
from datetime import datetime
from types import SimpleNamespace as NS

from minty_core.config import ConfigStore
from minty_core.events import EventBus
from minty_core.policy import Guard
from minty_core.telegram.push import PushRouter
from minty_core.toolkits import ToolkitRouter
from tests.plugin_helpers import make_host
from tests.test_telegram_gateway import OWNER, build


def runtime(tmp_path, quiet=False):
    gw, fake, *_ = build(tmp_path)
    router = PushRouter(gw, EventBus(), quiet=lambda now: quiet, state_path=tmp_path / "held.json", clock=lambda: datetime(2026, 10, 29, 23, 0))
    return NS(gateway=gw, push=router), fake


async def test_it_sends_to_the_paired_chat_even_in_quiet_hours_because_you_asked(tmp_path):
    rt, fake = runtime(tmp_path, quiet=True)
    host = make_host(tmp_path / "h", services={"telegram": rt})
    out = await host.call("phone_tell", {"text": "Milk, eggs, bread"})
    assert out.startswith("Sent") and fake.texts(OWNER) == ["[laptop] Milk, eggs, bread"]


async def test_the_chat_is_the_users_own_never_one_the_model_names(tmp_path):
    rt, fake = runtime(tmp_path)
    host = make_host(tmp_path / "h", services={"telegram": rt})
    await host.call("phone_tell", {"text": "hi", "chat_id": 999, "to": "someone else"})
    assert [m["chat_id"] for m in fake.sent] == [OWNER]


async def test_without_telegram_running_it_says_how_to_turn_it_on_instead_of_pretending(tmp_path):
    host = make_host(tmp_path, services={})
    out = await host.call("phone_tell", {"text": "hi"})
    assert "not running" in out and "telegram.enabled" in out and "set-key telegram" in out


async def test_with_no_chat_paired_it_says_so(tmp_path):
    rt = NS(gateway=NS(allowed=set(), suspended=False), push=None)
    host = make_host(tmp_path, services={"telegram": rt})
    assert "paired" in await host.call("phone_tell", {"text": "hi"})


async def test_in_private_mode_nothing_goes_to_telegram(tmp_path):
    rt, fake = runtime(tmp_path)
    host = make_host(tmp_path / "h", services={"telegram": rt, "is_private": lambda: True})
    out = await host.call("phone_tell", {"text": "my bank pin is hidden"})
    assert "private" in out and fake.sent == []


async def test_an_empty_message_asks_what_to_send(tmp_path):
    rt, fake = runtime(tmp_path)
    host = make_host(tmp_path / "h", services={"telegram": rt})
    assert "what" in (await host.call("phone_tell", {"text": "  "})).lower() and fake.sent == []


async def test_asking_for_telegram_in_your_own_words_is_not_asked_again_but_doing_it_unprompted_is(tmp_path):
    host = make_host(tmp_path / "h", services={})
    store = ConfigStore(tmp_path / "s")
    store.load()
    data = store.config.model_dump(mode="json")
    data["safety"]["mode"] = "commits"
    store.update(data)
    tool, asked = host.get_tool("phone_tell"), []

    async def confirm(question):
        asked.append(question)
        return False

    guard = Guard(store)
    d = await guard.authorize(tool, "phone_tell", {"text": "hi"}, confirm, request="send me the shopping list on telegram")
    assert d.allowed and asked == []
    d = await guard.authorize(tool, "phone_tell", {"text": "hi"}, confirm, request="what is the weather")
    assert not d.allowed and asked  # nothing in your words asked for a message on your phone


async def test_the_model_is_offered_the_tool_when_you_mention_telegram(tmp_path):
    host = make_host(tmp_path, services={})
    router = ToolkitRouter(lambda: {p["name"]: p["tools"] for p in host.list_plugins() if p["enabled"] and not p["error"]})
    assert "phone_tell" in router.tool_names("send me that on telegram")
