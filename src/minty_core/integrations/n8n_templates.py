"""Three n8n workflow templates (690), written as files for you to import in n8n (Workflows → Import from file). They are inactive,
contain no code, shell or file nodes, and no credentials: the Telegram node names a credential you create in n8n yourself.
The node field names follow n8n's workflow format as I know it; they have not been imported into a running n8n here."""
from __future__ import annotations

import json
import re
import uuid

from minty_core.web.urls import check_web_url

NAMES = ("daily-weather-to-telegram", "rss-to-telegram", "webhook-to-telegram")
_CHAT = re.compile(r"-?\d{5,20}")
CRED = {"telegramApi": {"id": "", "name": "Telegram account"}}


def _node(name: str, type_: str, version: float, params: dict, pos: tuple[int, int], creds: dict | None = None) -> dict:
    node = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "minty/" + name)), "name": name, "type": type_,
            "typeVersion": version, "position": list(pos)}
    if creds:
        node["credentials"] = creds
    return node


def _telegram(chat: str, text: str, pos) -> dict:
    return _node("Send to Telegram", "n8n-nodes-base.telegram", 1.2, {"chatId": chat, "text": text, "additionalFields": {}}, pos,
                 CRED)


def _chain(*names: str) -> dict:
    return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]} for a, b in zip(names, names[1:])}


def build_template(name: str, chat_id: str = "", url: str = "", city_lat: float | None = None, city_lon: float | None = None,
                   hour: int = 7, minute: int = 30) -> str:
    if name not in NAMES:
        raise ValueError("I have no template called that. The templates are: " + ", ".join(NAMES) + ".")
    if not _CHAT.fullmatch(str(chat_id)):
        raise ValueError("The Telegram chat id is a number (5 to 20 digits, a minus sign for groups).")
    if name == "daily-weather-to-telegram":
        if city_lat is None or city_lon is None:
            raise ValueError("Give the latitude and longitude of your place.")
        lat, lon = float(city_lat), float(city_lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError("Give a latitude (-90 to 90), a longitude (-180 to 180), an hour and a minute.")
        api = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.3f}&longitude={lon:.3f}"
               "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=auto&forecast_days=1")
        nodes = [_node("Every morning", "n8n-nodes-base.scheduleTrigger", 1.2,
                       {"rule": {"interval": [{"field": "cronExpression", "expression": f"{int(minute)} {int(hour)} * * *"}]}}, (0, 0)),
                 _node("Get forecast", "n8n-nodes-base.httpRequest", 4.2, {"url": api, "options": {}}, (240, 0)),
                 _telegram(chat_id, "=Today: high {{ $json.daily.temperature_2m_max[0] }}, low {{ $json.daily.temperature_2m_min[0] }}, "
                                    "rain chance {{ $json.daily.precipitation_probability_max[0] }}%", (480, 0))]
        conns = _chain("Every morning", "Get forecast", "Send to Telegram")
    elif name == "rss-to-telegram":
        feed = check_web_url(url)
        if not feed.lower().startswith("https://"):
            raise ValueError("The feed address must be https.")
        nodes = [_node("New item", "n8n-nodes-base.rssFeedReadTrigger", 1,
                       {"pollTimes": {"item": [{"mode": "everyX", "value": 1, "unit": "hours"}]}, "feedUrl": feed}, (0, 0)),
                 _telegram(chat_id, "={{ $json.title }}\n{{ $json.link }}", (240, 0))]
        conns = _chain("New item", "Send to Telegram")
    else:
        slug = "minty-" + uuid.uuid5(uuid.NAMESPACE_URL, "minty/webhook/" + chat_id).hex[:12]
        nodes = [_node("Incoming ping", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": slug, "options": {}}, (0, 0)),
                 _telegram(chat_id, "=Webhook received: {{ $json.body.message }}", (240, 0))]
        conns = _chain("Incoming ping", "Send to Telegram")
    wf = {"name": "Minty: " + name.replace("-", " "), "nodes": nodes, "connections": conns, "active": False, "settings": {},
          "pinData": {}}
    return json.dumps(wf, indent=2, ensure_ascii=False)
