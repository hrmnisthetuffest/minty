import math

from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2
from minty_core.recipes import RecipeError, Recipes, parse_days


def _minutes(value, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if math.isfinite(number) and number >= 0 else default


def register(ctx):
    def start(goal: str, extra: dict):
        starter = ctx.services.get("start_mission")
        if starter is None:
            raise RuntimeError("missions are unavailable")
        return starter(goal, extra)

    catch_up = _minutes((ctx.settings or {}).get("catch_up_minutes"), 120)
    recipes = ctx.state.get("recipes")  # one open database across reloads
    if recipes is None:
        recipes = ctx.state["recipes"] = Recipes(ctx.config_dir / "recipes.sqlite", start=start, catch_up_minutes=catch_up)
    else:
        recipes.rebind(start, catch_up)
    ctx.services["recipes"] = recipes
    ctx.services["recipe_loops"] = recipes.loops

    jobs = ctx.services.get("jobs")
    if jobs is not None:
        def finished(job) -> None:  # a recipe's mission has ended: close its run
            payload = getattr(job, "payload", None) or {}
            if payload.get("recipe") and payload.get("run"):
                recipes.finished(int(payload["run"]), job.status, getattr(job, "result", "") or "")

        old = ctx.state.get("listener")
        if old is not None and old in jobs.on_finish:
            jobs.on_finish.remove(old)  # a reload must not listen twice
        jobs.on_finish.append(finished)
        ctx.state["listener"] = finished

    def describe_create(args) -> str:
        days = str(args.get("days") or "daily")
        when = "every day" if days == "daily" else days
        return (f"Set up a standing automation '{args.get('name')}' that runs {when} at {args.get('at')}: "
                f"{str(args.get('goal', ''))[:240]}. Each run is a mission that spends model time, each risky step in it still asks "
                f"you, and it pauses itself after repeated failures. Set it up?")

    async def create(args):
        try:
            r = recipes.create(str(args.get("name") or ""), str(args.get("goal") or ""), str(args.get("at") or ""),
                               str(args.get("days") or "daily"), int(args.get("max_runs_per_day") or 1),
                               int(args.get("max_failures") or 3))
        except (RecipeError, ValueError) as e:
            return f"Error: {e}"
        return (f"Created recipe {r.name}: {r.days} at {r.at}. It runs only while Minty is running; a run missed while the "
                "computer is off is skipped. Say 'run recipe " + r.name + " now' to try it.")

    def line(r) -> str:
        state = f"PAUSED ({r.paused_reason})" if r.paused else "on"
        return f"{r.name}: {r.days} at {r.at}, {state}"

    async def list_(args):
        name = str(args.get("name") or "").strip()
        if name:
            r = recipes.get(name)
            if r is None:
                return f"No recipe {name}."
            runs = recipes.runs(name, 8)
            body = "\n".join(f"- {x.day} {x.slot}: {x.status}" + (f" ({x.summary})" if x.summary else "") for x in runs)
            return f"{line(r)}\nGoal: {r.goal}\nRecent runs:\n{body or '- none yet'}"
        found = recipes.all()
        return "\n".join(line(r) for r in found) if found else "No recipes yet."

    async def run_now(args):
        if ctx.services.get("start_mission") is None:
            return "Recipes are unavailable: missions are not connected."
        try:
            run = recipes.run_now(str(args.get("name") or ""))
        except RecipeError as e:
            return f"Error: {e}"
        if run.status != "started":
            return f"It could not start: {run.summary}"
        return f"Started a run of {run.recipe} (mission {run.job_id}). I'll tell you how it went."

    def manage_risk(args) -> int:
        return R2 if str(args.get("action")) == "delete" else R1

    async def manage(args):
        name, action = str(args.get("name") or ""), str(args.get("action") or "")
        if action not in ("pause", "resume", "delete"):
            return "Say pause, resume or delete."
        done = {"pause": lambda: recipes.pause(name), "resume": lambda: recipes.resume(name),
                "delete": lambda: recipes.delete(name)}[action]()
        if not done:
            return f"No recipe {name}."
        return {"pause": f"Paused {name}.", "resume": f"Resumed {name}.", "delete": f"Deleted {name}."}[action]

    s = {"type": "string"}
    return [
        Tool("create", "Set up an automation that runs a goal on a schedule, e.g. make and queue a reel every day at 18:00. "
                       "name: lowercase-with-dashes; at: HH:MM; days: daily, weekdays, weekends or mon,wed,fri.",
             {"type": "object", "properties": {"name": s, "goal": s, "at": s, "days": s, "max_runs_per_day": {"type": "integer"},
                                               "max_failures": {"type": "integer"}}, "required": ["name", "goal", "at"]},
             create, risk=3, describe=describe_create),
        Tool("list", "The automations that are set up, or one by name with its recent runs.",
             {"type": "object", "properties": {"name": s}}, list_),
        Tool("run_now", "Run an automation once now.", {"type": "object", "properties": {"name": s}, "required": ["name"]},
             run_now, risk=R1),
        Tool("manage", "Pause, resume or delete an automation.",
             {"type": "object", "properties": {"name": s, "action": s}, "required": ["name", "action"]}, manage,
             risk=manage_risk),
    ]
