"""Life tools: dashboards, the briefing, the weekly review, goals and the check-in. They read the coach hub and the
money ledger, say when there is not enough logged, and invent nothing."""
from datetime import date, datetime

from minty_core import life
from minty_core.finance import Ledger
from minty_core.plugins.host import Tool


def register(ctx):
    def stores():
        cfg = ctx.services["config"]()
        if not cfg.life.review:
            raise ValueError("The life review is switched off (life.review).")
        hub = ctx.services.get("health")
        if hub is None:
            raise ValueError("The health store is switched off, so there is nothing to review.")
        ledger = ctx.services.get("finance")
        if ledger is None:
            import tempfile
            from pathlib import Path

            ledger = Ledger(Path(tempfile.mkdtemp()) / "none.db")  # an empty ledger: money views stay empty
        return hub, ledger, hub._clock()

    def view(args):
        hub, ledger, now = stores()
        return life.dashboard(str(args.get("view") or "training"), hub, ledger, now, date.fromtimestamp(now))

    def briefing(args):
        hub, ledger, now = stores()
        return life.briefing(hub, ledger, now, date.fromtimestamp(now))

    def review(args):
        hub, ledger, now = stores()
        return life.weekly_review(hub, ledger, now, date.fromtimestamp(now)).text

    def goal(args):
        hub, _, now = stores()
        action = str(args.get("action") or "list")
        today = date.fromtimestamp(now)
        if action == "add":
            life.add_okr(hub, str(args.get("objective") or "Goal"), str(args["key_result"]), float(args["target"]),
                         unit=str(args.get("unit") or ""), due=str(args["due"]),
                         source=str(args.get("source") or "manual"))
            return f"Saved the goal '{args['key_result']}'."
        if action == "update":
            hub.update_okr(int(args["id"]), float(args["current"]))
            return "Updated."
        if action == "remove":
            hub.remove_okr(int(args["id"]))
            return "Removed."
        rows = life.okr_progress(hub, now, today)
        return "\n".join(f"{r['id']}: {r['objective']} - {r['key_result']}: {r['current']:g} of {r['target']:g} "
                         f"{r['unit']} ({r['pct']}%, {r['pace']}, {r['weeks_left']} weeks left)" for r in rows) \
            or "No goals yet."

    def checkin(args):
        hub, _, now = stores()
        life.save_checkin(hub, datetime.fromtimestamp(now).strftime("%Y-%m-%d"),
                          energy=args.get("energy") or None,
                          priority_done=args.get("priority_done") if "priority_done" in args else None)
        return "Noted."

    def schema(**props):
        return {"type": "object", "properties": {k: v for k, (v, _) in props.items()},
                "required": [k for k, (_, req) in props.items() if req]}

    s = {"type": "string"}
    return [
        Tool("view", "One of three cards: training, recovery or money.",
             schema(view=({"type": "string", "enum": list(life.VIEWS)}, True)), view, sensitive=True),
        Tool("briefing", "A 60-second briefing of the week so far.", schema(), briefing, sensitive=True),
        Tool("review", "The honest weekly review: wins, misses, one suggestion.", schema(), review, sensitive=True),
        Tool("goal", "Goals with key results: action add, update, list, remove (objective, key_result, target, "
                     "unit, due, source, id, current).",
             schema(action=({"type": "string", "enum": ["add", "update", "list", "remove"]}, True),
                    objective=(s, False), key_result=(s, False), target=(s, False), unit=(s, False), due=(s, False),
                    source=(s, False), id=({"type": "integer"}, False), current=(s, False)), goal, risk=1,
             sensitive=True),
        Tool("checkin", "Record today's check-in (energy low/ok/high, priority_done).",
             schema(energy=({"type": "string", "enum": ["low", "ok", "high"]}, False),
                    priority_done=({"type": "boolean"}, False)), checkin, risk=1, sensitive=True),
    ]
