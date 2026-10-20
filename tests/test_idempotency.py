"""A repeated side effect happens once within the window (W1-08)."""
import asyncio

from minty_core.config import SafetyConfig
from minty_core.idempotency import IdempotencyStore
from minty_core.plugins.host import Tool, _Loaded
from tests.plugin_helpers import make_host


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_store_returns_a_recent_success_and_forgets_it_after_the_window(tmp_path):
    clock = Clock()
    s = IdempotencyStore(tmp_path / "idem.sqlite", clock)
    key = IdempotencyStore.key("mail_send", {"to": "a", "body": "hi"})
    assert s.lookup(key, 120) is None
    s.record(key, "Sent.")
    clock.now += 30
    assert s.lookup(key, 120) == ("Sent.", 30)
    clock.now += 100
    assert s.lookup(key, 120) is None
    assert IdempotencyStore(tmp_path / "idem.sqlite", clock).lookup(key, 500) is not None  # survives a restart


def test_the_key_ignores_order_and_spacing_but_not_content():
    a = IdempotencyStore.key("mail_send", {"to": "a", "body": "hello  there"})
    assert a == IdempotencyStore.key("mail_send", {"body": "hello there", "to": "a"})
    assert a != IdempotencyStore.key("mail_send", {"body": "hello there", "to": "b"})


def host_with(tmp_path, tool, window=120.0, clock=None):
    """A real PluginHost with one extra plugin ("t") holding the tool under test."""
    host = make_host(tmp_path / "cfg")
    host._services["idempotency"] = IdempotencyStore(tmp_path / "idem.sqlite", clock or Clock())
    host._services["idempotency_window"] = lambda: window
    host._plugins["t"] = _Loaded(name="t", enabled=True, tools={"t_send": tool})
    return host


async def test_a_repeated_send_it_sends_once(tmp_path):
    sent = []

    async def send(args):
        sent.append(args["to"])
        return f"Sent to {args['to']}."

    tool = Tool("send", "Send a thing.", {"type": "object"}, send, idempotent=True)
    host = host_with(tmp_path, tool)
    first = await host.call("t_send", {"to": "a"})
    again = await host.call("t_send", {"to": "a"})
    other = await host.call("t_send", {"to": "b"})
    assert first == "Sent to a." and sent == ["a", "b"]
    assert again.startswith("Sent to a.") and "already done" in again and "not repeated" in again
    assert other == "Sent to b."


async def test_two_requests_at_the_same_moment_run_once(tmp_path):
    runs = []

    async def slow_send(args):
        runs.append(1)
        await asyncio.sleep(0.05)
        return "Sent."

    tool = Tool("send", "Send.", {"type": "object"}, slow_send, idempotent=True)
    host = host_with(tmp_path, tool)
    results = await asyncio.gather(host.call("t_send", {"to": "a"}), host.call("t_send", {"to": "a"}))
    assert len(runs) == 1 and sum("already done" in r for r in results) == 1


async def test_a_failure_is_not_remembered_and_the_window_can_be_zero(tmp_path):
    calls = []

    async def flaky(args):
        calls.append(1)
        return "Error: no network" if len(calls) == 1 else "Sent."

    tool = Tool("send", "Send.", {"type": "object"}, flaky, idempotent=True)
    host = host_with(tmp_path, tool)
    assert (await host.call("t_send", {"to": "a"})).startswith("Error")
    assert await host.call("t_send", {"to": "a"}) == "Sent."  # tried again, the failure was not kept
    off = host_with(tmp_path / "off", tool, window=0)
    await off.call("t_send", {"to": "z"})
    await off.call("t_send", {"to": "z"})
    assert len(calls) == 4  # with the window at 0 nothing is de-duplicated


def test_the_window_is_a_safety_setting():
    assert SafetyConfig().idempotency_window_s == 120
    assert SafetyConfig(idempotency_window_s=0).idempotency_window_s == 0


def test_the_marked_tools_exist_and_are_marked(tmp_path):
    from minty_core.plugins.host import IDEMPOTENT_TOOLS
    host = make_host(tmp_path / "cfg", plugins={n: {"enabled": True} for n in ("apis", "files", "installer", "n8n")})
    missing = [n for n in IDEMPOTENT_TOOLS if host.get_tool(n) is None or not host.get_tool(n).idempotent]
    assert not missing, missing
