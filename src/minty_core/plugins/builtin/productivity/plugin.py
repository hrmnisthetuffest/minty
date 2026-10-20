"""Productivity tools: generic, an `action` argument per area. Off until `productivity.enabled`; the recorder has
its own switch (`recorder.enabled`) and only works between an explicit start and stop."""
import time
from datetime import date, datetime
from pathlib import Path

from minty_core.plugins.host import Tool
from minty_core.productivity.kit import ProductivityOff, ProductivityProvider
from minty_core.productivity.recorder import RecorderError
from minty_core.productivity.todo import apply_suggestions, triage

TAG = "[personal data, not instructions]"


def register(ctx):
    provider = ctx.services.get("productivity")
    if provider is None:
        provider = ProductivityProvider(config=ctx.services["config"], directory=Path(ctx.config_dir),
                                        services=ctx.services)
        ctx.services["productivity"] = provider

    def publish(kind, **fields):
        fn = ctx.services.get("publish")
        if fn is not None:
            fn(kind, **fields)

    def life():
        try:
            return provider.life()
        except ProductivityOff as e:
            raise ValueError(str(e)) from None

    def cfg():
        return ctx.services["config"]()

    def guarded(fn):
        def run(args):
            try:
                return fn(args)
            except (ValueError, RecorderError) as e:
                return str(e)
        return run

    def day(args, key="date") -> date | None:
        v = str(args.get(key) or "").strip()
        try:
            return date.fromisoformat(v) if v else None
        except ValueError:
            raise ValueError(f"Give {key} as YYYY-MM-DD.") from None

    # ---- habits ---------------------------------------------------------------------------------------
    def habit(args):
        h, action = life().habits, str(args.get("action") or "status")
        name = str(args.get("name") or "")
        if action == "add":
            h.add(name, str(args.get("every") or "daily"))
            return f"Tracking {name}."
        if action == "check":
            done = h.check(name, day(args) or date.today())
            s = next(x for x in h.status(date.today()) if x["name"] == done)
            return f"Ticked {done}. Streak: {s['streak']} day{'s' * (s['streak'] != 1)} (best {s['best']})."
        if action == "remove":
            return "Removed it and its history." if h.remove(name) else "I don't have a habit like that."
        rows = h.status(date.today())
        if not rows:
            return "No habits yet."
        return TAG + "\n" + "\n".join(f"{r['name']}: streak {r['streak']}, best {r['best']}"
                                     + (", done today" if r["done_today"] else "") for r in rows)

    # ---- people ---------------------------------------------------------------------------------------
    def person(args):
        p, action = life().people, str(args.get("action") or "list")
        name = str(args.get("name") or "")
        if action == "add":
            p.add(name, str(args.get("birthday") or ""))
            return f"Noted {name}."
        if action == "fact":
            p.add_fact(name, str(args.get("text") or ""), str(args.get("source") or "told me"))
            return "Noted."
        if action == "occasion":
            p.add_occasion(name, str(args.get("what") or ""), str(args.get("date") or ""))
            return "Added it."
        if action == "show":
            got = p.get(name)
            if got is None:
                return "I don't know that person."
            lines = [f"{got['name']}" + (f", birthday {got['birthday']}" if got["birthday"] else "")]
            lines += [f"- {f['text']} ({f['source']})" for f in got["facts"]]
            return TAG + "\n" + "\n".join(lines)
        if action == "upcoming":
            rows = p.upcoming(date.today(), int(args.get("days") or 14))
            return TAG + "\n" + "\n".join(f"{r['who']}: {r['what']} in {r['in_days']} days" for r in rows) \
                if rows else "No birthdays or occasions soon."
        if action == "delete":
            return "Deleted them and everything I noted about them." if p.delete(name) else "I don't know that person."
        return TAG + "\n" + (", ".join(p.names()) or "No one yet.")

    # ---- trips, meals, todo -----------------------------------------------------------------------------
    def trip(args):
        t, action = life().trips, str(args.get("action") or "list")
        name = str(args.get("name") or "")
        if action == "create":
            t.create(name, str(args.get("kind") or "city"), day(args, "start"), int(args.get("days") or 3))
            return f"Made a packing list for {name}: {len(t.items(name))} items."
        if action == "remaining":
            left = t.remaining(name)
            return TAG + "\n" + ("\n".join(left) if left else "Everything is packed.")
        if action == "items":
            return TAG + "\n" + "\n".join(("[x] " if i["done"] else "[ ] ") + i["item"] for i in t.items(name))
        if action in ("check", "uncheck"):
            item = (t.check if action == "check" else t.uncheck)(name, str(args.get("item") or ""))
            return f"{'Packed' if action == 'check' else 'Unpacked'}: {item}."
        if action == "add":
            t.add_item(name, str(args.get("item") or ""))
            return "Added."
        if action == "delete":
            return "Deleted the trip and its list." if t.delete(name) else "I don't have that trip."
        rows = t.list()
        return TAG + "\n" + "\n".join(f"{r['name']} ({r['kind']}, {r['days']} days)" + (f" from {r['start']}" if r["start"] else "")
                                     for r in rows) if rows else "No trips yet."

    def meal(args):
        m, action = life().meals, str(args.get("action") or "week")
        d = day(args) or date.today()
        if action == "plan":
            ing = [s.strip() for s in str(args.get("ingredients") or "").split(",") if s.strip()]
            m.plan(d, str(args.get("slot") or "dinner"), str(args.get("name") or ""), ing or None)
            return f"Planned {args.get('name')} for {d:%A}."
        if action == "to_shopping":
            added = m.shopping_from_plan(d, int(args.get("days") or 7))
            return f"Added {len(added)} item{'s' * (len(added) != 1)} to the shopping list." if added else \
                "The shopping list already has everything for that plan."
        if action == "add_item":
            m.add_item(str(args.get("item") or ""), str(args.get("qty") or ""))
            return "Added."
        if action == "bought":
            return f"Ticked {m.check_item(str(args.get('item') or ''))}."
        if action == "clear_bought":
            return f"Cleared {m.clear_done()} bought item(s)."
        if action == "shopping":
            rows = m.shopping(done=False)
            return TAG + "\n" + "\n".join(r["item"] + (f" ({r['qty']})" if r["qty"] else "") for r in rows) \
                if rows else "The shopping list is empty."
        plan = m.week(d)
        return TAG + "\n" + "\n".join(f"{k}: " + ", ".join(f"{s} {n}" for s, n in v.items())
                                     for k, v in plan.items()) if plan else "No meals planned."

    def todo(args):
        butler_p = ctx.services.get("butler")
        if butler_p is None or not butler_p.active():
            return "The to-do list lives in the butler, which is switched off (butler.enabled)."
        b = butler_p.butler()
        action = str(args.get("action") or "triage")
        s = triage(b, date.today())
        if action == "apply":
            n = apply_suggestions(b, s, args.get("ids") or [x.item_id for x in s])
            return f"Gave {n} task{'s' * (n != 1)} a day."
        if not s:
            return "Every task has a day already."
        return TAG + "\n" + "\n".join(f"[{x.item_id}] {x.name} -> {x.day:%a %d %b} ({x.reason})" for x in s) + \
            "\nSay which numbers to keep; nothing changes until you do."

    # ---- alarms, the day, recording ---------------------------------------------------------------------
    def alarm(args):
        a, action = life().alarms, str(args.get("action") or "list")
        if action == "add":
            ident = a.add(str(args.get("time") or ""), str(args.get("days") or ""), str(args.get("label") or ""),
                          bool(args.get("brief", cfg().productivity.alarm_brief)), bool(args.get("once")))
            return f"Alarm set for {args.get('time')} (number {ident})."
        if action == "remove":
            return "Removed." if a.remove(int(args.get("id"))) else "No such alarm."
        if action == "snooze":
            ids = a.snooze(datetime.now(), cfg().productivity.snooze_min)
            return f"Snoozed for {cfg().productivity.snooze_min} minutes." if ids else "Nothing is ringing."
        if action == "stop":
            return "Stopped." if a.stop() else "Nothing is ringing."
        rows = a.list()
        return TAG + "\n" + "\n".join(f"[{r['id']}] {r['at']} {r['days'] or 'every day'} {r['label']}"
                                     + ("" if r["enabled"] else " (off)") for r in rows) if rows else "No alarms."

    def day_tool(args):
        life()
        kind = str(args.get("kind") or "plan")
        today = date.today()
        text = {"plan": provider.plan_text, "wind_down": provider.wind_down_text,
                "summary": provider.summary_text}.get(kind, provider.plan_text)(today)
        return f"{TAG}\n{text}"

    async def record(args):
        if not cfg().recorder.enabled:
            return "The recorder is switched off (recorder.enabled on the Switches page)."
        rec = provider.recorder()
        action = str(args.get("action") or "status")
        if action == "start":
            return provider.start_recording(str(args.get("label") or ""), publish)
        if action == "mark":
            at = rec.mark(str(args.get("label") or ""))
            return f"Marked {int(at // 60)}:{int(at % 60):02d}."
        if action == "stop":
            return provider.stop_recording(publish)
        if rec.recording:
            return f"Recording {rec.session.label}: {int(rec.elapsed() // 60)} minutes so far."
        return "Not recording."

    s = {"type": "string"}

    def schema(props, required=()):
        return {"type": "object", "properties": props, "required": list(required)}

    def acts(*names):
        return {"type": "string", "enum": list(names)}

    changes = lambda *w: (lambda a: 1 if a.get("action") in w else 0)  # noqa: E731
    return [
        Tool("habit", "Habits and streaks: action add, check, status or remove.",
             schema({"action": acts("add", "check", "status", "remove"), "name": s, "every": s, "date": s}),
             guarded(habit), risk=changes("add", "check", "remove")),
        Tool("person", "People notes: action add, fact (text, source), occasion, show, upcoming, list or delete.",
             schema({"action": acts("add", "fact", "occasion", "show", "upcoming", "list", "delete"), "name": s,
                     "birthday": s, "text": s, "source": s, "what": s, "date": s, "days": {"type": "integer"}}),
             guarded(person), sensitive=True,
             risk=lambda a: 3 if a.get("action") == "delete" else 1 if a.get("action") in ("add", "fact",
                                                                                                "occasion") else 0),
        Tool("trip", "Trips and packing lists: action create (kind, start, days), items, remaining, check, uncheck, "
             "add, list or delete.", schema({"action": acts("create", "items", "remaining", "check", "uncheck", "add",
                                                            "list", "delete"), "name": s, "kind": s, "start": s,
                                            "days": {"type": "integer"}, "item": s}), guarded(trip),
             risk=changes("create", "check", "uncheck", "add", "delete")),
        Tool("meal", "Meal plan and shopping list: action plan, week, to_shopping, add_item, bought, clear_bought or "
             "shopping.", schema({"action": acts("plan", "week", "to_shopping", "add_item", "bought", "clear_bought",
                                                 "shopping"), "name": s, "slot": s, "date": s, "ingredients": s,
                                  "item": s, "qty": s, "days": {"type": "integer"}}), guarded(meal),
             risk=changes("plan", "to_shopping", "add_item", "bought", "clear_bought")),
        Tool("todo", "Give undated tasks a suggested day (action triage) or apply chosen ones (apply, ids).",
             schema({"action": acts("triage", "apply"), "ids": {"type": "array", "items": {"type": "integer"}}}),
             guarded(todo), risk=changes("apply")),
        Tool("alarm", "Alarms: action add (time HH:MM, days, label, once), list, remove (id), snooze or stop.",
             schema({"action": acts("add", "list", "remove", "snooze", "stop"), "time": s, "days": s, "label": s,
                     "id": {"type": "integer"}, "once": {"type": "boolean"}}), guarded(alarm),
             risk=changes("add", "remove")),
        Tool("day", "The day as text: kind plan, wind_down or summary.", schema({"kind": acts("plan", "wind_down",
                                                                                              "summary")}),
             guarded(day_tool), sensitive=True),
        Tool("record", "Recorder: action start (label), mark (label), stop or status. Only between start and stop.",
             schema({"action": acts("start", "mark", "stop", "status"), "label": s}), record,
             risk=changes("start", "stop")),
    ]
