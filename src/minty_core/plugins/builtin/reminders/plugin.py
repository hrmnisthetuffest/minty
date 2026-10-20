import re
from datetime import datetime, timedelta

from minty_core.plugins.host import Tool
from minty_core.policy import R1

MAX_AHEAD = timedelta(days=366)


def parse_at(text: str, now: datetime) -> datetime:
    """'2026-10-12T15:30', '2026-10-12 15:30', or '15:30' (today, or tomorrow if that time has passed)."""
    text = str(text or "").strip()
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if m:
        due = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    try:
        return datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        raise ValueError("give the time as an ISO date-time such as 2026-10-12T15:30, or HH:MM") from None


def when_words(due: datetime, now: datetime) -> str:
    clock = due.strftime("%H:%M")
    if due.date() == now.date():
        return f"today at {clock}"
    if due.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return f"on {due:%A %d %B} at {clock}"


def register(ctx):
    def scheduler():
        s = ctx.services.get("scheduler")
        if s is None:
            raise RuntimeError("the scheduler is not running")
        return s

    def now() -> datetime:
        return ctx.services.get("now", datetime.now)()

    async def set_reminder(args):
        text = " ".join(str(args.get("text") or "").split())
        if not text:
            return "Error: say what to remind about."
        current = now()
        if args.get("in_minutes") is not None:
            minutes = float(args["in_minutes"])
            if not 0 < minutes <= MAX_AHEAD.total_seconds() / 60:
                return "Error: in_minutes must be more than 0 and at most a year."
            due = current + timedelta(minutes=minutes)
        elif args.get("at"):
            try:
                due = parse_at(args["at"], current)
            except ValueError as e:
                return f"Error: {e}"
        else:
            return "Error: give `at` (a time or date-time) or `in_minutes`."
        if due <= current - timedelta(seconds=30) or due - current > MAX_AHEAD:
            return "Error: that time has already passed or is more than a year away."
        item = scheduler().add(text, max(due, current), user_set=True, kind="reminder")
        return f"Okay, reminder {item.id}: I'll remind you {when_words(due, current)} to {text}."

    async def list_reminders(args):
        items = [i for i in scheduler().waiting() if i.user_set]
        if not items:
            return "No reminders waiting."
        current = now()
        return " ".join(f"Reminder {i.id}, {when_words(i.due, current)}: {i.text}." for i in items[:10])

    async def cancel_reminder(args):
        return "Cancelled." if scheduler().cancel(int(args["id"])) else "No waiting reminder with that id."

    async def snooze_reminder(args):
        item = scheduler().snooze(int(args["id"]), int(args.get("minutes") or 10))
        return f"Okay, again {when_words(item.due, now())}." if item else "No reminder with that id."

    return [
        Tool("set", "Set a reminder: `at` as an ISO date-time (work it out with the clock tool) or HH:MM, or "
                    "`in_minutes`. It speaks at that moment.",
             {"type": "object", "properties": {"text": {"type": "string"}, "at": {"type": "string"},
                                               "in_minutes": {"type": "number"}}, "required": ["text"]},
             set_reminder, risk=R1),
        Tool("list", "List the reminders that are waiting.", {"type": "object", "properties": {}}, list_reminders),
        Tool("cancel", "Cancel a waiting reminder by id.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, cancel_reminder,
             risk=R1),
        Tool("snooze", "Move a reminder later by some minutes (default 10).",
             {"type": "object", "properties": {"id": {"type": "integer"}, "minutes": {"type": "integer"}},
              "required": ["id"]}, snooze_reminder, risk=R1),
    ]
