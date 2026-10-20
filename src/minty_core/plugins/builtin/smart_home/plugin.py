import re

from minty_core.httpclient import get_client
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R3

ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+")
CONTROLLABLE = {"light", "switch", "fan", "scene", "climate", "media_player", "lock", "cover", "input_boolean", "script"}
SWITCHABLE = {"light", "switch", "fan", "climate", "media_player", "input_boolean"}
SECURE = {"lock", "cover", "alarm_control_panel"}
SECURE_ACTIONS = {("lock", "lock"): "lock", ("lock", "unlock"): "unlock",
                  ("cover", "open"): "open_cover", ("cover", "close"): "close_cover"}
NOT_CONFIGURED = "Home Assistant isn't configured. Set its URL and access token in the panel."


def _entity(value) -> tuple[str, str]:
    entity_id = str(value or "")
    if not ENTITY.fullmatch(entity_id):
        raise ValueError(f"invalid entity id {entity_id!r}")
    return entity_id, entity_id.split(".")[0]


def register(ctx):
    def settings():
        url, token = str(ctx.settings.get("url") or "").rstrip("/"), ctx.secret("token")
        return (url, token) if url and token else None

    async def call(method, path, body=None):
        url, token = settings()
        resp = await get_client().request(method, url + path, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if resp.status_code in (401, 403):
            raise RuntimeError("Home Assistant rejected the token")
        resp.raise_for_status()
        return resp.json()

    async def service(domain, name, data):
        await call("POST", f"/api/services/{domain}/{name}", data)

    def configured(fn):
        async def wrapper(args):
            return await fn(args) if settings() else NOT_CONFIGURED
        return wrapper

    @configured
    async def list_devices(args):
        wanted = str(args.get("domain") or "").strip()
        rows = []
        for s in await call("GET", "/api/states"):
            domain = s["entity_id"].split(".")[0]
            if (wanted and domain == wanted) or (not wanted and domain in CONTROLLABLE):
                rows.append(f"{s['entity_id']}: {s['attributes'].get('friendly_name', s['entity_id'])} ({s['state']})")
        return "\n".join(rows[:60]) or "No matching devices."

    @configured
    async def get_state(args):
        entity_id, _ = _entity(args.get("entity_id"))
        s = await call("GET", f"/api/states/{entity_id}")
        unit = s["attributes"].get("unit_of_measurement")
        return f"{s['attributes'].get('friendly_name', entity_id)}: {s['state']}" + (f" {unit}" if unit else "")

    @configured
    async def set_device(args):
        entity_id, domain = _entity(args.get("entity_id"))
        if domain in SECURE:
            return f"I can't switch {domain} devices with this tool. Locks and covers need control_secure."
        if domain not in SWITCHABLE:
            return f"I can't switch {domain} devices."
        if not isinstance(args.get("on"), bool):
            raise ValueError("on must be true or false")
        await service(domain, "turn_on" if args.get("on") else "turn_off", {"entity_id": entity_id})
        return f"Turned {'on' if args.get('on') else 'off'} {entity_id}."

    @configured
    async def set_brightness(args):
        entity_id, domain = _entity(args.get("entity_id"))
        percent = int(args["percent"])
        if domain != "light" or not 0 <= percent <= 100:
            raise ValueError("brightness needs a light and a percent from 0 to 100")
        await service("light", "turn_on", {"entity_id": entity_id, "brightness_pct": percent})
        return f"Set {entity_id} to {percent} percent."

    @configured
    async def run_scene(args):
        entity_id, domain = _entity(args.get("entity_id"))
        if domain != "scene":
            raise ValueError("run_scene needs a scene entity")
        await service("scene", "turn_on", {"entity_id": entity_id})
        return f"Activated {entity_id}."

    @configured
    async def control_secure(args):
        entity_id, domain = _entity(args.get("entity_id"))
        action = SECURE_ACTIONS.get((domain, str(args.get("action"))))
        if action is None:
            return "I can't do that. Only lock/unlock for locks and open/close for covers are allowed."
        await service(domain, action, {"entity_id": entity_id})
        return f"Done: {args['action']} {entity_id}."

    def describe_secure(args):
        return f"Should I {args.get('action')} {args.get('entity_id')}?"

    obj = lambda **props: {"type": "object", "properties": props, "required": list(props)}
    entity = {"type": "string"}
    return [
        Tool("list_devices", "List smart-home devices with entity ids and states. Optional domain filter.",
             {"type": "object", "properties": {"domain": {"type": "string"}}}, list_devices),
        Tool("get_state", "Get one entity's state, e.g. a sensor reading.", obj(entity_id=entity), get_state),
        Tool("set_device", "Turn a light, switch, fan or similar device on or off.",
             obj(entity_id=entity, on={"type": "boolean"}), set_device, risk=R1),
        Tool("set_brightness", "Set a light's brightness percent.",
             obj(entity_id=entity, percent={"type": "integer"}), set_brightness, risk=R1),
        Tool("run_scene", "Activate a scene.", obj(entity_id=entity), run_scene, risk=R1),
        Tool("control_secure", "Lock/unlock a lock or open/close a cover (garage door, blinds). Needs confirmation.",
             obj(entity_id=entity, action={"type": "string", "enum": ["lock", "unlock", "open", "close"]}),
             control_secure, risky=True, risk=R3, describe=describe_secure),  # locks and doors: never silent
    ]
