"""The notifier's real channels: the screen, the voice, the phone and the digest, and its policy from your settings."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.datewords import parse_hhmm
from minty_core.notifier import Channel, Notifier, Policy

DEFAULT_ROUTES = {"info": ("record",), "notable": ("record", "digest", "panel"),
                  "important": ("record", "panel", "say", "phone"), "urgent": ("record", "panel", "say", "phone")}
SILENT_ROUTES = {name: ("record", "panel") if name != "info" else ("record",) for name in DEFAULT_ROUTES}


def build_notifier(path: Path, store, bus, services: dict, *, clock: Callable[[], float] = time.time,
                   hour_of: Callable[[float], int] = lambda t: datetime.fromtimestamp(t).hour) -> Notifier:
    def digest(event, text: str) -> None:
        scheduler = services.get("scheduler")
        if scheduler is not None:  # held by the scheduler's own gate and delivered as one digest
            scheduler.add(text, datetime.now(), user_set=False, kind="digest", key=f"notifier:{event.source}:{event.key}")

    channels = [
        Channel("record", lambda event, text: None),  # the notifier keeps its own record
        Channel("digest", digest),
        Channel("panel", lambda event, text: bus.publish("notice", message=text)),  # silent: the screen and the panel
        Channel("say", lambda event, text: bus.publish("notify", text=text, local=True), loud=True),  # said and shown here, never mirrored
        Channel("phone", lambda event, text: bus.publish("phone", text=text, urgent=event.severity == "urgent"), loud=True, private=False),
    ]

    def policy() -> Policy:
        cfg, proactive = store.config.notify, store.config.proactive
        routes = dict(DEFAULT_ROUTES if proactive.enabled else SILENT_ROUTES)  # "stop doing things by yourself": nothing is said unprompted
        if not cfg.phone:
            routes = {name: tuple(c for c in chans if c != "phone") for name, chans in routes.items()}
        return Policy(quiet_from=parse_hhmm(proactive.night_from).hour, quiet_to=parse_hhmm(proactive.night_to).hour,
                      urgent_bypass_quiet=cfg.urgent_bypass_quiet, max_important_per_hour=cfg.max_important_per_hour,
                      max_urgent_per_hour=cfg.max_urgent_per_hour, remind_after_h=cfg.remind_after_h, max_reminders=cfg.max_reminders, routes=routes)

    return Notifier(path, channels, policy, clock=clock, hour_of=hour_of)
