import hashlib
import time
from datetime import datetime

from minty_core.attention import Attention
from minty_core.attention_sources import butler_loops, mission_loops
from minty_core.plugins.host import Tool
from minty_core.policy import R1


def register(ctx):
    def nudge(text: str) -> None:
        scheduler = ctx.services.get("scheduler")
        if scheduler is None:
            return
        key = "attention:" + hashlib.sha1(f"{text}{int(time.time())}".encode()).hexdigest()[:12]
        scheduler.add(text, datetime.now(), user_set=False, kind="nudge", key=key)  # the scheduler's gate decides when it is said

    def outbox():
        source = ctx.services.get("outbox_attention")
        return source() if source is not None else []

    attention = Attention(ctx.config_dir / "attention.sqlite", {
        "missions": lambda: mission_loops(ctx.services.get("jobs")),
        "butler": lambda: butler_loops(ctx.services.get("butler")),
        "outbox": outbox,
        "recipes": lambda: (ctx.services.get("recipe_loops") or (lambda: []))(),
    }, nudge=nudge)
    ctx.services["attention"] = attention

    async def review(args):
        return attention.review()

    async def dismiss(args):
        key = str(args.get("key") or "")
        return f"Dismissed {key}." if attention.dismiss(key) else f"No open item {key!r}."

    async def snooze(args):
        key, hours = str(args.get("key") or ""), float(args.get("hours") or 24)
        return f"Snoozed {key} for {hours:g} hours." if attention.snooze(key, hours) else f"No open item {key!r}."

    s = {"type": "string"}
    return [
        Tool("review", "What needs the user right now: blocked missions, e-mails that may not have gone, overdue items, "
                       "follow-ups due. Each has a key for dismiss or snooze.", {"type": "object", "properties": {}}, review),
        Tool("dismiss", "Stop mentioning an item from the attention list, by its key.",
             {"type": "object", "properties": {"key": s}, "required": ["key"]}, dismiss, risk=R1),
        Tool("snooze", "Stop mentioning an item for some hours (default 24).",
             {"type": "object", "properties": {"key": s, "hours": {"type": "number"}}, "required": ["key"]}, snooze, risk=R1),
    ]
