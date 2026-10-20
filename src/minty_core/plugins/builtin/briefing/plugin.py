import asyncio
import logging
from datetime import datetime, timedelta

from minty_core.plugins.host import Tool

log = logging.getLogger(__name__)
MAX_CHARS = 700


def register(ctx):
    def now() -> datetime:
        return ctx.services.get("now", datetime.now)()

    async def tool(name: str, args: dict | None = None) -> str:
        call = ctx.services.get("call_tool")
        if call is None:
            return ""
        try:
            out = await asyncio.wait_for(call(name, args or {}), 25)
        except Exception:  # noqa: BLE001 — a missing or slow part never ruins the brief
            return ""
        return "" if out.startswith("Error") or out.startswith("Unknown") or "unknown tool" in out else out.strip()

    async def compose() -> str:
        current = now()
        parts = [f"Good morning. It's {current:%A}, {current.day} {current:%B}."]
        weather = await tool("weather_get_weather")
        if weather and not weather.startswith("I don't have a location"):
            parts.append(weather)
        scheduler = ctx.services.get("scheduler")
        if scheduler is not None:
            today = [i for i in scheduler.waiting() if i.user_set and i.due.date() == current.date()
                     and i.due > current - timedelta(minutes=1)]
            if today:
                parts.append("Reminders today: " + "; ".join(f"{i.text} at {i.due:%H:%M}" for i in today[:3]) + ".")
        homework = await tool("school_homework")
        if homework and not homework.startswith("No homework"):
            parts.append("Homework: " + " ".join(homework.split(". ")[:2]).rstrip(".") + ".")
        mail = await tool("mail_unread", {"limit": 3})
        if mail and not mail.startswith("No unread"):
            lines = [line for line in mail.splitlines() if line and not line.startswith("[")]
            if lines:
                top = lines[1].split(": ", 1)[-1] if len(lines) > 1 else ""
                parts.append(f"Mail: {lines[0].rstrip('.')}" + (f"; most important: {top}." if top else "."))
        text = " ".join(parts)
        return text if len(text) <= MAX_CHARS else text[:MAX_CHARS].rsplit(" ", 1)[0] + "."

    async def brief_me(args):
        return await compose()

    async def morning_brief():
        scheduler = ctx.services.get("scheduler")
        if scheduler is None:
            return
        text = await compose()
        current = now()
        daystart = ctx.services.get("daystart")
        if daystart is not None:
            daystart.mark_done(current.date())  # the scheduled brief counts as today's day start
        scheduler.add(text, current, user_set=False, kind="brief", key=f"brief:{current.date()}", once=True)
        scheduler.tick(current)

    ctx.services["brief_compose"] = compose
    scheduler = ctx.services.get("scheduler")
    if scheduler is not None:
        scheduler.register_action("morning_brief", morning_brief)

    return [Tool("now", "A short brief of the user's day: weather, today's reminders, homework, unread mail. For "
                        "'brief me' or 'what's on today'.",
                 {"type": "object", "properties": {}}, brief_me, sensitive=True)]
