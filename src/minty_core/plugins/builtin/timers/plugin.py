import asyncio
from pathlib import Path

from minty_core.plugins.host import Tool
from minty_core.focus import Focus
from minty_core.timerstore import TimerStore


def register(ctx):
    store = TimerStore(Path(ctx.config_dir) / "timers.json")
    ctx.services["timers"] = store  # the control API (the overlay) reads it
    focus = Focus(Path(ctx.config_dir) / "focus.json")
    ctx.services["focus"] = focus
    publish = ctx.services.get("publish")

    def say(t) -> str:
        if t.late:
            return f"Your {t.label} timer finished while I wasn't running."
        return "Your timer is done." if t.label == "timer" else f"Your {t.label} timer is done."

    def fire() -> None:
        for t in store.fire_due():
            if publish is not None:
                publish("notify", text=say(t), urgent=True)  # a timer is never held back by focus mode
            else:
                ctx.notify(say(t))
            if publish is not None:
                publish("timer", state="done", id=t.id, label=t.label, kind=t.kind, late=t.late)
        schedule()

    def schedule() -> None:
        """One wake-up for the earliest timer; re-run after every change and when the host is reloaded."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop yet: the first tool call or the app's loop arms it
        old = ctx.state.get("handle")
        if old is not None:
            old.cancel()
        wait = store.next_due_in()
        ctx.state["handle"] = loop.call_later(max(0.0, wait), fire) if wait is not None else None

    ctx.services["timers_schedule"] = schedule

    async def set_timer(args):
        seconds = float(args["seconds"])
        t = store.add(seconds, str(args.get("label") or ""), float(args.get("repeat_seconds") or 0),
                      str(args.get("kind") or "timer"))
        schedule()
        if publish is not None:
            publish("timer", state="set", id=t.id, label=t.label, kind=t.kind, due=t.due)
        every = f", repeating every {t.repeat_s:g} seconds" if t.repeat_s else ""
        return f"Timer {t.id} set: {t.label}, {seconds:g} seconds{every}."

    async def list_timers(args):
        schedule()  # arm whatever was on disk when this process started
        now = store.clock()
        timers = store.all()
        if not timers:
            return "No timers running."
        return " ".join(f"Timer {t.id} ({t.label}): {max(0, round(t.remaining(now)))} seconds left."
                        for t in sorted(timers, key=lambda t: t.id))

    async def cancel_timer(args):
        t = store.cancel(int(args["id"]))
        if t is None:
            return "No timer with that id."
        schedule()
        if publish is not None:
            publish("timer", state="cancelled", id=t.id, label=t.label, kind=t.kind)
        return f"Cancelled the {t.label} timer."

    async def start_focus(args):
        minutes = float(args.get("minutes") or 25)
        label = str(args.get("label") or "focus").strip() or "focus"
        t = store.add(minutes * 60, label, kind="focus")
        focus.start(minutes, label)
        schedule()
        if publish is not None:
            publish("timer", state="set", id=t.id, label=t.label, kind="focus", due=t.due)
        return (f"Focus started for {minutes:g} minutes: {label}. Notices wait until it ends, except timers. "
                "Say end focus to stop it.")

    async def stop_focus(args):
        focus.stop()
        for t in store.all():
            if t.kind == "focus":
                store.cancel(t.id)
        schedule()
        return "Focus ended. Anything that waited comes back now."

    obj = {"type": "object", "properties": {}}
    return [
        Tool("set_timer", "Start a countdown timer. Convert minutes/hours to seconds; repeat_seconds repeats it.",
             {"type": "object", "properties": {"seconds": {"type": "number"}, "label": {"type": "string"},
                                               "repeat_seconds": {"type": "number"},
                                               "kind": {"type": "string", "enum": ["timer", "study", "focus"]}},
              "required": ["seconds"]}, set_timer),
        Tool("list_timers", "List running timers and time left.", obj, list_timers),
        Tool("cancel_timer", "Cancel a running timer by id.",
             {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}, cancel_timer),
        Tool("start_focus", "Start focus mode (minutes, label): a timer, and non-urgent notices wait.",
             {"type": "object", "properties": {"minutes": {"type": "number"}, "label": {"type": "string"}}},
             start_focus, risk=1),
        Tool("stop_focus", "End focus mode now.", obj, stop_focus, risk=1),
    ]
