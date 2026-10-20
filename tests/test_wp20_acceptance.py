"""WP-20 acceptance (plan-master): calendar import is read-only and idempotent; the LAN scan runs only on request and only on private
ranges you list. Plus the MCP promises: a model cannot add servers or allow tools, and a changed or order-giving tool does not run."""
import ast
import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

from minty_core.integrations import ics
from tests.test_integrations_plugin import FEED, Rig
from tests.test_mcp_registry_plugin import Rig as McpRig

IST = ZoneInfo("Asia/Kolkata")


async def test_calendar_import_is_read_only_and_idempotent(tmp_path):
    r = Rig(tmp_path)
    await r.call("calendar", {"action": "add_feed", "name": "school", "url": FEED})
    store = r.host._services["integrations"].calendar()
    snapshot = store.dump()
    for _ in range(3):
        await r.call("calendar", {"action": "refresh"})
    assert store.dump() == snapshot  # three more imports: nothing moved
    tree = ast.parse(inspect.getsource(ics))
    verbs = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not verbs & {"post", "put", "patch", "request", "urlopen"}  # there is no way to write to a calendar
    assert r.fetched == [FEED] * 4  # only ever the one address, fetched (GET) once per import


async def test_the_network_scan_never_runs_by_itself_and_never_leaves_the_listed_private_ranges(tmp_path):
    r = Rig(tmp_path)
    p = r.host._services["integrations"]
    for _ in range(3):  # the background loop, many passes, hours apart
        p._clock = lambda t=[datetime(2026, 10, 19, 10, 0, tzinfo=IST).timestamp()]: t.__setitem__(0, t[0] + 4 * 3600) or t[0]
        await p.tick()
    assert r.net.calls == []  # not one ping from the loop
    await r.call("lan", {"action": "scan"})
    targets = {c[-1] for c in r.net.calls if c[0] == "ping"}
    assert targets and all(t.startswith("192.168.1.") for t in targets)
    for bad, why in ((["8.8.8.0/24"], "not a private"), (["10.0.0.0/8"], "too wide"), (["192.168.0.0/16"], "too wide"),
                     (["169.254.0.0/24"], "not a private")):
        w = Rig(tmp_path / bad[0].replace("/", "_"), lan_ranges=bad)
        assert why in await w.call("lan", {"action": "scan"})
        assert w.net.calls == []


async def test_a_model_cannot_add_a_server_or_allow_a_tool_and_a_changed_or_order_giving_tool_does_not_run(tmp_path):
    r = McpRig(tmp_path, "poison")
    names = {t.name for p in r.host._plugins.values() if p.name == "mcp" for t in p.tools.values()}
    assert not any(w in n for n in names for w in ("add", "allow", "remove", "revoke", "install"))
    assert "not allowed" in await r.call("call", {"server": "fake", "tool": "echo", "arguments": {"text": "x"}})
    listing = await r.call("servers", {})
    assert "echo" not in listing  # a tool the server offers is not even listed until you have looked at it and allowed it
    await r.allow()
    assert "gives orders to a model" in await r.call("servers", {})  # an allowed tool with a poisoned description is still flagged
