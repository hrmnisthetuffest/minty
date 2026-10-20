import json
import logging
import re
from urllib.parse import quote, urlsplit

import httpx

from minty_core.plugins.host import Tool
from minty_core.policy import R1, R3

log = logging.getLogger(__name__)

TIMEOUT = 20.0
MAX_OUTPUT = 5000
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_HOOK = re.compile(r"[A-Za-z0-9_\-/]{1,120}")
_TYPE = re.compile(r"[A-Za-z0-9@_.\-]+")

# Types and versions used by current n8n 1.x. n8n rejects what it does not know, and the error comes back to the model.
COMMON_NODES = {
    "n8n-nodes-base.manualTrigger": 1,
    "n8n-nodes-base.scheduleTrigger": 1.2,
    "n8n-nodes-base.webhook": 2,
    "n8n-nodes-base.httpRequest": 4.2,
    "n8n-nodes-base.set": 3.4,
    "n8n-nodes-base.if": 2.2,
    "n8n-nodes-base.code": 2,
    "n8n-nodes-base.emailSend": 2.1,
    "n8n-nodes-base.slack": 2.3,
    "n8n-nodes-base.telegram": 1.2,
    "n8n-nodes-base.googleSheets": 4.5,
    "n8n-nodes-base.merge": 3,
    "n8n-nodes-base.wait": 1.1,
    "n8n-nodes-base.respondToWebhook": 1.1,
}

GUIDE = (
    "Workflow JSON: nodes is a list of {name, type, typeVersion, position:[x,y], parameters}; connections maps a "
    "source node NAME to {main:[[{node:<target name>, type:'main', index:0}]]}. Start with a trigger node "
    "(manualTrigger, scheduleTrigger, webhook). Workflows are created inactive."
)


class N8nError(Exception):
    pass


def _shorten(value, limit: int = MAX_OUTPUT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " ...[cut]"


def validate_workflow(nodes, connections) -> str | None:
    """Return a problem description, or None when the structure is plausible for n8n."""
    if not isinstance(nodes, list) or not nodes or len(nodes) > 60:
        return "nodes must be a list of 1 to 60 nodes"
    names = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            return f"node {i} is not an object"
        for key in ("name", "type", "position"):
            if key not in node:
                return f"node {i} is missing '{key}'"
        if not isinstance(node["name"], str) or not node["name"].strip() or node["name"] in names:
            return f"node {i} needs a unique non-empty name"
        names.add(node["name"])
        if not isinstance(node["type"], str) or not _TYPE.fullmatch(node["type"]):
            return f"node {node['name']!r} has an invalid type"
        pos = node["position"]
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(p, (int, float)) for p in pos)):
            return f"node {node['name']!r} position must be [x, y]"
        node.setdefault("parameters", {})
        node.setdefault("typeVersion", COMMON_NODES.get(node["type"], 1))
    if not isinstance(connections, dict):
        return "connections must be an object"
    for source, outputs in connections.items():
        if source not in names:
            return f"connection from unknown node {source!r}"
        text = json.dumps(outputs)
        for target in re.findall(r'"node":\s*"((?:[^"\\]|\\.)*)"', text):
            if target not in names:
                return f"connection to unknown node {target!r}"
    return None


def register(ctx):
    settings = ctx.settings or {}
    base = str(settings.get("base_url") or "http://127.0.0.1:5678").rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("n8n base_url must be an http(s) address without credentials")
    client_holder: dict = {}

    def client() -> httpx.AsyncClient:
        # trust_env off: a proxy variable must not redirect the n8n key; no redirects: the key stays on this host.
        if "c" not in client_holder:
            client_holder["c"] = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False)
        return client_holder["c"]

    async def call(method: str, path: str, *, body=None, params=None, auth=True):
        headers = {"Accept": "application/json"}
        if auth:
            key = ctx.secret("api_key")
            if not key:
                raise N8nError("n8n API key missing. In n8n create one (Settings > n8n API), then run: "
                               "minty-core set-key plugin.n8n.api_key")
            headers["X-N8N-API-KEY"] = key
        try:
            resp = await client().request(method, base + path, json=body, params=params, headers=headers)
        except httpx.TimeoutException:
            raise N8nError("n8n did not answer in time") from None
        except httpx.HTTPError:
            raise N8nError(f"could not reach n8n at {parts.netloc}. Is it running?") from None
        if resp.status_code in (401, 403):
            raise N8nError("n8n rejected the API key")
        if resp.status_code == 404:
            raise N8nError("not found in n8n")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", "")
            except ValueError:
                detail = ""
            raise N8nError(f"n8n error {resp.status_code}: {_shorten(detail or resp.text, 300)}")
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def guarded(fn):
        async def run(args):
            try:
                return await fn(args)
            except N8nError as e:
                return f"Error: {e}"
        return run

    def wid(args) -> str:
        value = str(args.get("id") or "")
        if not _ID.fullmatch(value):
            raise N8nError("give a workflow id")
        return value

    async def list_workflows(args):
        data = await call("GET", "/api/v1/workflows", params={"limit": 50})
        rows = [f"{w['id']}: {w.get('name')} ({'active' if w.get('active') else 'inactive'})"
                for w in data.get("data", [])]
        return "\n".join(rows) or "No workflows yet."

    async def get_workflow(args):
        w = await call("GET", f"/api/v1/workflows/{wid(args)}")
        summary = {"id": w.get("id"), "name": w.get("name"), "active": w.get("active"),
                   "nodes": [{"name": n.get("name"), "type": n.get("type")} for n in w.get("nodes", [])],
                   "connections": w.get("connections")}
        return _shorten(summary)

    async def create_workflow(args):
        name = str(args.get("name") or "").strip()[:100]
        if not name:
            raise N8nError("give the workflow a name")
        try:
            nodes = json.loads(args["nodes"]) if isinstance(args.get("nodes"), str) else args.get("nodes")
            connections = args.get("connections") or {}
            connections = json.loads(connections) if isinstance(connections, str) else connections
        except (ValueError, KeyError):
            raise N8nError("nodes and connections must be valid JSON") from None
        problem = validate_workflow(nodes, connections)
        if problem:
            raise N8nError(f"{problem}. {GUIDE}")
        made = await call("POST", "/api/v1/workflows",
                          body={"name": name, "nodes": nodes, "connections": connections, "settings": {}})
        return (f"Created workflow '{name}' (id {made.get('id')}), inactive. "
                f"Open it in n8n to review, or ask me to activate it.")

    async def activate(args):
        w = await call("POST", f"/api/v1/workflows/{wid(args)}/activate")
        return f"Workflow {w.get('id', args.get('id'))} is now active."

    async def deactivate(args):
        await call("POST", f"/api/v1/workflows/{wid(args)}/deactivate")
        return "Workflow deactivated."

    async def delete(args):
        await call("DELETE", f"/api/v1/workflows/{wid(args)}")
        return "Workflow deleted."

    async def executions(args):
        params = {"limit": 5}
        if args.get("id"):
            params["workflowId"] = wid(args)
        data = await call("GET", "/api/v1/executions", params=params)
        rows = [f"#{e.get('id')} workflow {e.get('workflowId')}: {e.get('status')} at {e.get('startedAt')}"
                for e in data.get("data", [])]
        return "\n".join(rows) or "No executions yet."

    async def trigger(args):
        path = str(args.get("path") or "").strip("/")
        if not _HOOK.fullmatch(path) or ".." in path:
            raise N8nError("give the webhook path (the part after /webhook/)")
        body = args.get("payload")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = {"text": body}
        data = await call("POST", f"/webhook/{quote(path, safe='/')}", body=body if body is not None else {},
                          auth=False)
        return "Triggered. " + _shorten(data, 600)


    idp = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    return [
        Tool("list_workflows", "List the user's n8n workflows.", {"type": "object", "properties": {}},
             guarded(list_workflows)),
        Tool("get_workflow", "Show a workflow's nodes and connections.", idp, guarded(get_workflow)),
        Tool("create_workflow", "Create an n8n workflow; it stays INACTIVE until the user approves activation. nodes: "
                                "JSON [{name, type, typeVersion, position:[x,y], parameters}]; connections: {source "
                                "NAME: {main:[[{node:<target>, type:'main', index:0}]]}}. Start with a trigger: "
                                "manualTrigger, scheduleTrigger or webhook.",
             {"type": "object", "properties": {
                 "name": {"type": "string"},
                 "nodes": {"type": "string", "description": "JSON array of nodes"},
                 "connections": {"type": "string", "description": "JSON object of connections"}},
              "required": ["name", "nodes"]}, guarded(create_workflow), risk=R1),
        Tool("activate_workflow", "Turn a workflow on so its triggers start running.", idp, guarded(activate),
             risky=True, describe=lambda a: f"Should I activate n8n workflow {a.get('id')}?"),
        Tool("deactivate_workflow", "Turn a workflow off.", idp, guarded(deactivate), risk=R1),
        Tool("delete_workflow", "Delete a workflow.", idp, guarded(delete), risky=True, risk=R3,
             describe=lambda a: f"Should I delete n8n workflow {a.get('id')}?"),
        Tool("list_executions", "Recent workflow runs and whether they succeeded.",
             {"type": "object", "properties": {"id": {"type": "string"}}}, guarded(executions)),
        Tool("trigger_webhook", "Run a workflow that starts with a Webhook node, sending an optional JSON payload.",
             {"type": "object", "properties": {"path": {"type": "string"}, "payload": {"type": "string"}},
              "required": ["path"]}, guarded(trigger), risky=True,
             describe=lambda a: f"Should I trigger the n8n webhook {a.get('path')}?"),
    ]
