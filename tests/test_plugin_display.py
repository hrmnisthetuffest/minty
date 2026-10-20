import pytest

from minty_core.plugins.host import BUILTIN_PLUGIN_DIR, PluginHost
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore


def host_with_publish(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    events = []
    services = {"publish": lambda type_, **data: events.append({"type": type_, **data})}
    host = PluginHost([BUILTIN_PLUGIN_DIR], store, SecretsStore(tmp_path), tmp_path, services=services)
    host.reload()
    return host, events


async def test_show_publishes_a_display_event(tmp_path):
    host, events = host_with_publish(tmp_path)
    assert await host.call("display_show", {"title": "Todo", "content": "- milk"}) == "Shown on screen."
    assert events == [{"type": "display", "title": "Todo", "content": "- milk"}]


async def test_show_rejects_empty_and_caps_length(tmp_path):
    host, events = host_with_publish(tmp_path)
    assert (await host.call("display_show", {"content": "  "})).startswith("Error")
    await host.call("display_show", {"content": "x" * 50000})
    assert len(events[0]["content"]) == 6000


async def test_clear_and_url_checks(tmp_path, monkeypatch):
    host, events = host_with_publish(tmp_path)
    await host.call("display_clear", {})
    assert events[-1] == {"type": "display", "title": "", "content": ""}
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert (await host.call("display_open_url", {"url": "http://127.0.0.1:8123/admin"})).startswith("Error")
    assert opened == []
    assert not host.get_tool("display_show").risky
