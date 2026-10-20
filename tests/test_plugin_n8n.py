import json

import httpx
import pytest

from tests.plugin_helpers import make_host

REAL = httpx.AsyncClient


def host_for(tmp_path, monkeypatch, handler, key="n8n-secret", **settings):
    seen = []

    def respond(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL(transport=httpx.MockTransport(respond), **kw))
    secrets = {"plugin.n8n.api_key": key} if key else {}
    host = make_host(tmp_path, plugins={"n8n": {"enabled": True, **settings}}, secrets=secrets)
    return host, seen


NODES = [
    {"name": "Every morning", "type": "n8n-nodes-base.scheduleTrigger", "position": [0, 0],
     "parameters": {"rule": {"interval": [{"field": "days"}]}}},
    {"name": "Ping", "type": "n8n-nodes-base.httpRequest", "position": [240, 0],
     "parameters": {"url": "https://example.com/health"}},
]
CONNECTIONS = {"Every morning": {"main": [[{"node": "Ping", "type": "main", "index": 0}]]}}


async def test_create_workflow_is_inactive_and_sends_the_key(tmp_path, monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"id": "42"})

    host, seen = host_for(tmp_path, monkeypatch, handler)
    out = await host.call("n8n_create_workflow", {"name": "Morning ping", "nodes": json.dumps(NODES),
                                                   "connections": json.dumps(CONNECTIONS)})
    assert "id 42" in out and "inactive" in out
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/api/v1/workflows"
    assert req.headers["x-n8n-api-key"] == "n8n-secret"
    body = json.loads(req.content)
    assert body["name"] == "Morning ping" and body["settings"] == {} and "active" not in body
    assert body["nodes"][0]["typeVersion"] == 1.2 and body["nodes"][1]["typeVersion"] == 4.2


@pytest.mark.parametrize("nodes, connections, problem", [
    ("[]", "{}", "nodes must be"),
    (json.dumps([{"name": "a", "type": "x"}]), "{}", "missing 'position'"),
    (json.dumps(NODES), json.dumps({"Nope": {"main": []}}), "unknown node"),
    (json.dumps(NODES), json.dumps({"Ping": {"main": [[{"node": "Ghost"}]]}}), "unknown node 'Ghost'"),
    (json.dumps([NODES[0], dict(NODES[0])]), "{}", "unique"),
    ("not json", "{}", "valid JSON"),
])
async def test_invalid_workflows_never_reach_n8n(tmp_path, monkeypatch, nodes, connections, problem):
    host, seen = host_for(tmp_path, monkeypatch, lambda r: httpx.Response(200, json={}))
    out = await host.call("n8n_create_workflow", {"name": "x", "nodes": nodes, "connections": connections})
    assert out.startswith("Error") and problem in out and seen == []


async def test_activation_deletion_and_webhooks_need_a_spoken_yes(tmp_path, monkeypatch):
    host, _ = host_for(tmp_path, monkeypatch, lambda r: httpx.Response(200, json={}))
    for name in ("n8n_activate_workflow", "n8n_delete_workflow", "n8n_trigger_webhook"):
        assert host.get_tool(name).risky
    assert host.get_tool("n8n_activate_workflow").describe({"id": "7"}) == "Should I activate n8n workflow 7?"
    assert not host.get_tool("n8n_create_workflow").risky and not host.get_tool("n8n_list_workflows").risky


async def test_missing_key_and_unreachable_server_are_clean_errors(tmp_path, monkeypatch):
    host, _ = host_for(tmp_path / "a", monkeypatch, lambda r: httpx.Response(200, json={}), key=None)
    assert "API key missing" in await host.call("n8n_list_workflows", {})

    def down(req):
        raise httpx.ConnectError("refused")

    host2, _ = host_for(tmp_path / "b", monkeypatch, down)
    out = await host2.call("n8n_list_workflows", {})
    assert out.startswith("Error: could not reach n8n at 127.0.0.1:5678") and "n8n-secret" not in out


async def test_list_get_and_executions_render_compactly(tmp_path, monkeypatch):
    def handler(req):
        if req.url.path == "/api/v1/workflows":
            return httpx.Response(200, json={"data": [{"id": "1", "name": "A", "active": True},
                                                      {"id": "2", "name": "B", "active": False}]})
        if req.url.path == "/api/v1/workflows/1":
            return httpx.Response(200, json={"id": "1", "name": "A", "active": True, "nodes": NODES,
                                             "connections": CONNECTIONS})
        return httpx.Response(200, json={"data": [{"id": 9, "workflowId": "1", "status": "success",
                                                   "startedAt": "2026-10-10T08:00:00Z"}]})

    host, _ = host_for(tmp_path, monkeypatch, handler)
    assert await host.call("n8n_list_workflows", {}) == "1: A (active)\n2: B (inactive)"
    assert "scheduleTrigger" in await host.call("n8n_get_workflow", {"id": "1"})
    assert "#9 workflow 1: success" in await host.call("n8n_list_executions", {"id": "1"})
    assert (await host.call("n8n_get_workflow", {"id": "../etc"})).startswith("Error")


async def test_webhook_path_is_validated_and_sends_no_api_key(tmp_path, monkeypatch):
    host, seen = host_for(tmp_path, monkeypatch, lambda r: httpx.Response(200, json={"ok": True}))
    assert "Triggered" in await host.call("n8n_trigger_webhook", {"path": "daily/report", "payload": '{"a": 1}'})
    assert seen[0].url.path == "/webhook/daily/report" and "x-n8n-api-key" not in seen[0].headers
    assert (await host.call("n8n_trigger_webhook", {"path": "../admin"})).startswith("Error")


async def test_bad_base_url_disables_the_plugin_not_the_core(tmp_path, monkeypatch):
    host, _ = host_for(tmp_path, monkeypatch, lambda r: httpx.Response(200), base_url="ftp://x")
    assert host.get_tool("n8n_list_workflows") is None
