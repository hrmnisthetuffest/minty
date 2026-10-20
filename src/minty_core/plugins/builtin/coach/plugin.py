"""Coach tools: deterministic facts from the engine, each logged with the numbers and rules it came from.

Off until `coach.enabled` and until you agreed to the training domain; paused by "pause coaching". The model only ever
receives these results, never the workout rows.
"""
import statistics
from datetime import datetime

from minty_core.coach import engine, foods as foodlib, nutrition, planning, review, wellbeing
from minty_core.coach.briefs import Coach
from minty_core.plugins.host import Tool

DAY = 86400.0
BAND = "10-20 hard sets per muscle per week"


def register(ctx):
    def cfg():
        return ctx.services["config"]().coach

    def ready():
        """(hub, None) when coaching may run, else (None, the sentence that says why not)."""
        if not cfg().enabled:
            return None, "Coaching is switched off (coach.enabled on the Switches page)."
        hub = ctx.services.get("health")
        if hub is None:
            return None, "The health store is switched off, so there is nothing to coach from."
        if hub.profile().get("coach_paused") == "1":
            return None, "Coaching is paused. Say 'resume coaching' to bring it back."
        if not hub.consent("training"):
            return None, "You haven't agreed to collecting training data yet, so there's nothing to coach from."
        return hub, None

    def week(args):
        hub, why = ready()
        if hub is None:
            return why
        now = hub._clock()
        sessions = engine.load_sessions(hub)
        recent = [s for s in sessions if now - 7 * DAY < s.started <= now]
        sets = engine.hard_sets_by_muscle(sessions, now, 7)
        parts = [f"Last 7 days: {len(recent)} session{'s' * (len(recent) != 1)}."]
        shown = sorted(((m, n) for m, n in sets.items() if m != "other"), key=lambda kv: -kv[1])[:6]
        if shown:
            parts.append("Hard sets: " + ", ".join(f"{m} {n} ({engine.volume_status(n)})" for m, n in shown) +
                         f" (band: {BAND}).")
        prs = [p for s in recent for p in engine.prs_in(s, [x for x in sessions if x.started < s.started])]
        if prs:
            parts.append("New estimated maxes: " + ", ".join(f"{p.exercise} {p.e1rm:.1f} kg" for p in prs[:4]) + ".")
        counts: dict[str, int] = {}
        for s in sessions:
            if now - 42 * DAY <= s.started:
                for x in s.sets:
                    if engine.is_working(x):
                        counts[x.exercise] = counts.get(x.exercise, 0) + 1
        stuck = [e for e, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:6] if engine.plateau(sessions, now, e)]
        if stuck:
            parts.append("No new best in six weeks: " + ", ".join(stuck[:3]) + ".")
        per_week = hub.profile().get("sessions_per_week", "")
        if per_week.isdigit():
            parts.append(f"Adherence over four weeks: {engine.adherence(sessions, now, int(per_week)):.0%} of "
                         f"{per_week} a week.")
        runs = [(a["started"], a["distance_m"]) for a in hub.rows("activities")
                if "run" in a["kind"].lower() and a["distance_m"] and a["residency"] != "local_only"]
        if runs:
            load = engine.running_load(runs, now)
            parts.append(f"Running: {load.this_week_km:.1f} km this week, {load.last_week_km:.1f} last week"
                         + (f" ({load.change_pct:+.0f}%, a big jump)." if load.flag else "."))
        sleep_nights = sum(1 for r in hub.rows("sleep") if now - 7 * DAY < r["start"] <= now and r["minutes"] < 360)
        hint = nutrition.recovery_hint(len(stuck), sleep_nights)
        if hint:
            parts.append(hint)
        if len([s for s in sessions if now - 28 * DAY <= s.started]) < 4:
            parts.append("There isn't much data yet, so treat this loosely.")
        text = " ".join(parts)
        hub.log_advice("week", text, text, [f"{BAND}", "PR: new estimated max beats every earlier one",
                                           "plateau: no new best in six weeks over four or more sessions"])
        return text

    def lift(args):
        hub, why = ready()
        if hub is None:
            return why
        query = str(args.get("exercise") or "").strip().lower()
        sessions = engine.load_sessions(hub)
        names: dict[str, int] = {}
        for s in sessions:
            for x in s.sets:
                if query and query in x.exercise.lower() and engine.is_working(x):
                    names[x.exercise] = names.get(x.exercise, 0) + 1
        if not names:
            return f"I have no lift matching '{query}' in your log."
        name = max(names, key=names.get)
        series = []
        for s in sessions:
            best, _ = engine._best_set([x for x in s.sets if x.exercise == name])
            if best:
                series.append(best)
        trend = " → ".join(f"{v:.0f}" for v in series[-6:])
        now = hub._clock()
        target = engine.next_targets(sessions, [name])
        text = (f"{name}: estimated max {max(series):.1f} kg, recent sessions {trend}. "
                + ("No new best in six weeks. " if engine.plateau(sessions, now, name) else "")
                + (f"Next: {target[0].weight_kg:g} kg x {target[0].reps} ({target[0].why})." if target else ""))
        hub.log_advice("lift", text, text, ["estimated max = weight x (1 + reps/30)", "double progression"])
        return text

    def next_session(args):
        hub, why = ready()
        if hub is None:
            return why
        return Coach(hub, clock=hub._clock).pre_workout(str(args.get("title") or "") or None).text

    def food(args):
        hub, why = ready()
        if hub is None:
            return why
        if not cfg().nutrition:
            return "Food coaching is switched off (coach.nutrition)."
        now = hub._clock()
        weights = [(r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"
                   and r["residency"] != "local_only"]
        latest = sorted(weights)[-1][1] if weights else 0
        day_floor = datetime.fromtimestamp(now - 21 * DAY).strftime("%Y-%m-%d")
        rows = [r for r in hub.rows("nutrition") if r["day"] >= day_floor and r["residency"] != "local_only"]
        parts = []
        band = nutrition.protein_band(latest)
        if band:
            avg = statistics.mean(r["protein_g"] for r in rows if r["protein_g"] is not None) if any(
                r["protein_g"] is not None for r in rows) else None
            parts.append(f"Protein: the usual band for {latest:g} kg is {band[0]}-{band[1]} g a day"
                         + (f"; you average {avg:.0f} g ({nutrition.protein_status(avg, band)})." if avg else "."))
        else:
            parts.append("I need a weight in your log to give a protein band.")
        est = nutrition.maintenance_estimate(weights, [(r["day"], r["kcal"]) for r in rows if r["kcal"] is not None],
                                             now)
        parts.append((f"Maintenance estimate: about {est.kcal} kcal ({est.confidence} confidence). {est.note}"
                      if est.kcal else est.note))
        water = [r["water_ml"] for r in rows if r["water_ml"]]
        parts.append(nutrition.hydration_hint(statistics.mean(water) if water else None, latest))
        target = nutrition.sleep_target(hub.age_band())
        nights = [r["minutes"] for r in hub.rows("sleep") if now - 7 * DAY < r["start"] <= now]
        if nights:
            avg_h = statistics.mean(nights) / 60
            parts.append(f"Sleep: {avg_h:.1f} h a night over {len(nights)} nights "
                         f"({nutrition.sleep_status(avg_h, target)}; usual range {target[0]:g}-{target[1]:g} h).")
        text = " ".join(parts)
        hub.log_advice("food", text, text, ["protein 1.4-2.0 g per kg (ISSN)", "7700 kcal per kg for the trend",
                                           "sleep 8-10 h teens, 7-9 h adults (AASM)"])
        return text

    def readiness(args):
        hub, why = ready()
        if hub is None:
            return why
        r = nutrition.readiness([(x["start"], x["minutes"]) for x in hub.rows("sleep")], hub._clock())
        return f"Sleep hint: {r.label}. {r.note}"

    EQUIPMENT_WORDS = {"gym": "gym", "barbell": "barbell", "dumbbell": "dumbbell", "machine": "machine",
                       "cable": "cable", "band": "bands", "bands": "bands", "bodyweight": "bodyweight",
                       "nothing": "bodyweight", "none": "bodyweight", "home": "bodyweight"}

    def equipment_from(text: str) -> set[str]:
        words = {EQUIPMENT_WORDS[w] for w in text.lower().replace(",", " ").split() if w in EQUIPMENT_WORDS}
        return words or {"gym"}

    def typical_week(sessions, now):
        recent = [s for s in sessions if now - 28 * DAY <= s.started <= now]
        by_day: dict[int, dict[str, int]] = {}
        for s in recent:
            by_day.setdefault(datetime.fromtimestamp(s.started).weekday(), {}).setdefault(s.title, 0)
            by_day[datetime.fromtimestamp(s.started).weekday()][s.title] += 1
        out = []
        for day, titles in sorted(by_day.items()):
            title, n = max(titles.items(), key=lambda kv: kv[1])
            if n >= 2:
                last = next(s for s in reversed(recent) if s.title == title)
                out.append((day, title, {engine.muscle_of(x.exercise) for x in last.sets
                                         if engine.muscle_of(x.exercise) != "other"}))
        return out

    def planning_ready():
        hub, why = ready()
        if hub is None:
            return None, why
        if not cfg().planning:
            return None, "Planning is switched off (coach.planning)."
        return hub, None

    def plan(args):
        hub, why = planning_ready()
        if hub is None:
            return why
        what = str(args.get("what") or "programme")
        now = hub._clock()
        sessions = engine.load_sessions(hub)
        if what == "programme":
            days = int(args.get("days") or hub.profile().get("sessions_per_week") or 3)
            equip = equipment_from(str(args.get("equipment") or hub.profile().get("equipment") or "gym"))
            prog = planning.build_programme(max(2, min(6, days)), equip)
            sets = planning.weekly_sets_in_programme(prog)
            text = prog.render() + " Weekly sets: " + ", ".join(f"{m} {n}" for m, n in sorted(sets.items())) + "."
            hub.log_advice("programme", text[:600], f"{days} days, equipment {sorted(equip)}",
                           ["split by days per week", "10-20 hard sets per muscle per week where possible"])
            return text
        if what == "deload":
            counts: dict[str, int] = {}
            for s in sessions:
                if now - 42 * DAY <= s.started:
                    for x in s.sets:
                        if engine.is_working(x):
                            counts[x.exercise] = counts.get(x.exercise, 0) + 1
            stuck = [e for e, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:6]
                     if engine.plateau(sessions, now, e)]
            short = sum(1 for r in hub.rows("sleep") if now - 7 * DAY < r["start"] <= now and r["minutes"] < 360)
            reasons = []
            if len(stuck) >= 2:
                reasons.append(f"{len(stuck)} lifts have stopped moving")
            if short >= 5:
                reasons.append(f"{short} of the last 7 nights were short")
            if not reasons and not args.get("force"):
                return ("I don't see a reason for a lighter week from your numbers (lifts still moving, sleep "
                        "fine). Say 'plan a lighter week anyway' if you want one.")
            d = planning.deload_plan(sessions, now, reasons or ["you asked for one"])
            hub.log_advice("deload", d.text[:600], "; ".join(reasons) or "asked", ["lighter week: 90% load, 60% sets"])
            return d.text
        if what == "goal":
            plan_ = planning.goal_plan(sessions, now, str(args.get("exercise") or ""),
                                       float(args.get("target") or 0), float(args.get("weeks") or 8))
            hub.log_advice("goal", plan_.text[:600], plan_.text, ["least-squares trend over 12 weeks"])
            return plan_.text
        if what == "rescue":
            week = typical_week(sessions, now)
            missed = str(args.get("missed") or "").lower()
            idx = next((i for i, w in enumerate(week) if w[1].lower() == missed), None)
            if idx is None:
                return "I can't find that session in your usual week, so I can't rescue it."
            return planning.rescue_plan(week, idx, datetime.fromtimestamp(now).weekday()).text
        raise ValueError("what is programme, deload, goal or rescue")

    def review_(args):
        hub, why = planning_ready()
        if hub is None:
            return why
        what, now = str(args.get("what") or "prs"), hub._clock()
        sessions = engine.load_sessions(hub)
        if what == "prs":
            b = planning.pr_board(sessions, now)
            lines = [f"{x.exercise}: {x.e1rm:.1f} kg estimated ({x.weight_kg:g} x {x.reps}, {x.day})"
                     for x in b.lifts[:6]]
            return " ".join(lines + [f"Milestone: {m}." for m in b.milestones]) or "No lifts logged yet."
        if what == "consistency":
            per = hub.profile().get("sessions_per_week", "")
            if not per.isdigit():
                return "Tell me how many sessions a week you aim for first (set sessions_per_week)."
            c = planning.consistency(sessions, now, int(per))
            return (f"Target {per} a week: {c.current_streak_weeks} week streak now, longest "
                    f"{c.longest_streak_weeks}; {c.score_pct}% of the target over the last four weeks.")
        if what == "balance":
            b = planning.balance_map(sessions, now)
            return ("Last 4 weeks, hard sets: " + ", ".join(f"{m} {n}" for m, n in sorted(b.sets.items())
                                                           if m != "other") + ". "
                    + (" ".join(b.flags) or "Pushing and pulling look balanced."))
        if what == "minutes":
            acts = [(a["started"], a["duration_s"] or 0) for a in hub.rows("activities") if a["residency"] != "local_only"]
            m = planning.active_minutes(sessions, acts, now)
            return f"{m.minutes} active minutes in the last 7 days ({m.pct_of_guideline:.0f}% of the 150-minute guideline)."
        if what == "works":
            return planning.what_works(review.weekly_series(hub, now))
        if what == "stall":
            return review.plateau_postmortem(hub, str(args.get("exercise") or ""), now)
        raise ValueError("what is prs, consistency, balance, minutes, works or stall")

    def injury(args):
        hub, why = planning_ready()
        if hub is None:
            return why
        action = str(args.get("action") or "list")
        if action == "log":
            iid = hub.log_injury(str(args.get("area") or ""), str(args.get("note") or ""))
            return (f"Logged (number {iid}). I can't diagnose or treat it; a doctor or physio can. Tell me when "
                    "they've cleared you and I'll show a gradual return ramp.")
        if action == "clear":
            hub.clear_injury(int(args.get("id") or 0))
            return "Marked as cleared. Ask for the return ramp when you're ready to train it again."
        if action == "ramp":
            return review.injury_ramp(hub, int(args.get("id") or 0), hub._clock())
        rows = hub.injuries()
        return "; ".join(f"{i['id']}: {i['area']} ({'cleared' if i['cleared_at'] else 'active'})"
                         for i in rows) or "No injuries logged."

    async def air(args):
        c = cfg()
        if not c.air_quality or c.latitude is None or c.longitude is None:
            return "The air-quality check is off (coach.air_quality plus a latitude and longitude)."
        from minty_core.coach.air import air_note

        client = ctx.services.get("http_client")
        if client is None:
            from minty_core.httpclient import get_client

            client = get_client()
        return await air_note(client, float(c.latitude), float(c.longitude))

    def nutrition_ready():
        hub, why = ready()
        if hub is None:
            return None, why
        if not cfg().nutrition:
            return None, "Food coaching is switched off (coach.nutrition)."
        return hub, None

    def filters(hub):
        allergies = {a for a in str(hub.profile().get("allergies", "")).lower().replace(",", " ").split() if a}
        diet = hub.profile().get("diet", "").lower()
        return allergies, diet in ("vegetarian", "vegan")

    def foods_(args):
        hub, why = nutrition_ready()
        if hub is None:
            return why
        action = str(args.get("action") or "list")
        if action == "adopt":
            n = foodlib.adopt_starter(hub)
            return (f"Added {n} common foods to your list as generic estimates; correct any of them with your own "
                    "numbers.") if n else "Your list already has the common foods."
        if action == "add":
            hub.add_food(str(args["name"]), str(args.get("serving") or ""), kcal=float(args.get("kcal") or 0),
                         protein_g=float(args.get("protein") or 0), carbs_g=float(args.get("carbs") or 0),
                         fat_g=float(args.get("fat") or 0), vegetarian=bool(args.get("vegetarian")),
                         allergens=str(args.get("allergens") or ""),
                         cost_minor=int(float(args["cost"]) * 100) if args.get("cost") else None)
            return f"Saved {args['name']} (your entry)."
        if action == "remove":
            return "Removed." if hub.remove_food(str(args.get("name") or "")) else "I don't have that food."
        if action == "recipe":
            items = []
            for part in str(args.get("items") or "").split(";"):
                if part.strip():
                    name, _, times = part.rpartition(" x")
                    items.append((name.strip() if name else part.strip(), float(times) if times else 1.0))
            hub.add_recipe(str(args["name"]), items)
            t = foodlib.recipe_totals(hub, str(args["name"]))
            return (f"Recipe {args['name']}: {t.kcal:g} kcal, {t.protein_g:g} g protein"
                    + (" (partly generic estimates)." if t.estimate else " (from your own entries)."))
        rows = hub.foods()
        return "; ".join(f"{f['name']} ({f['kcal']:g} kcal, {f['protein_g']:g} g protein, {f['source']})"
                         for f in rows[:20]) or "Your food list is empty. Say 'add the common foods' to start."

    def eat_next(args):
        hub, why = nutrition_ready()
        if hub is None:
            return why
        what = str(args.get("what") or "gap")
        allergies, veg = filters(hub)
        if what == "cost":
            ranked = foodlib.cost_per_10g(hub)
            return ("; ".join(f"{c.food}: {wellbeing_money(c.cost_minor_per_10g)} per 10 g protein"
                              for c in ranked[:6]) or "None of your foods has a price yet.")
        grams = float(args.get("grams") or 0)
        if grams <= 0:
            band = nutrition.protein_band(_latest_weight(hub))
            grams = float(band[0]) if what == "vegetarian" and band else 20.0
        picks = (foodlib.vegetarian_plan(hub, grams, allergies) if what == "vegetarian"
                 else foodlib.protein_gap(hub, grams, allergies, veg))
        if not picks:
            return "I have nothing in your food list that fits (add foods, or adopt the common ones)."
        text = "; ".join(f"{p.servings} x {p.food} ({p.protein_g:g} g protein, {p.kcal:g} kcal"
                         f"{', estimate' if p.estimate else ''})" for p in picks)
        hub.log_advice("food-suggestion", text[:600], f"target {grams:g} g protein",
                       ["foods from your own list only", "allergies and diet applied as filters"])
        return f"From your own list, toward {grams:g} g of protein: {text}."

    def wellbeing_money(minor: int) -> str:
        return f"{minor // 100}.{minor % 100:02d}"

    def _latest_weight(hub) -> float:
        rows = sorted((r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"
                      and r["residency"] != "local_only")
        return rows[-1][1] if rows else 0.0

    def wellbeing_(args):
        hub, why = nutrition_ready()
        if hub is None:
            return why
        what, now = str(args.get("what") or "weekly"), hub._clock()
        if what == "macros":
            weights = [(r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"]
            floor = datetime.fromtimestamp(now - 21 * DAY).strftime("%Y-%m-%d")
            intake = [(r["day"], r["kcal"]) for r in hub.rows("nutrition") if r["day"] >= floor and r["kcal"]]
            est = nutrition.maintenance_estimate(weights, intake, now)
            return wellbeing.macros_left(hub, datetime.fromtimestamp(now).strftime("%Y-%m-%d"), est.kcal)
        if what == "morning":
            return wellbeing.morning_note(hub, now)
        if what == "weekly":
            return wellbeing.weekly_report(hub, now)
        if what == "bedtime":
            bt = wellbeing.bedtime_consistency([r["start"] for r in hub.rows("sleep") if now - 14 * DAY < r["start"]])
            return (f"Bedtime consistency {bt.score}/100: about {bt.spread_min:.0f} minutes either side over "
                    f"{bt.n} nights." if bt.score is not None
                    else f"I need at least 7 nights to judge bedtime consistency; I have {bt.n}.")
        if what == "banking":
            nights = [r["minutes"] for r in hub.rows("sleep") if now - 14 * DAY < r["start"] <= now]
            if not nights:
                return "I need some logged nights to plan sleep banking."
            exam = hub.profile().get("exam_date", "")
            days = int(args.get("days_until") or 0)
            if not days and exam:
                days = (datetime.strptime(exam, "%Y-%m-%d").date() - datetime.fromtimestamp(now).date()).days
            if days <= 0:
                return "Tell me how many days until the heavy week (days_until), or set exam_date."
            return wellbeing.sleep_banking(statistics.mean(nights) / 60, nutrition.sleep_target(hub.age_band())[1],
                                           days)
        if what == "load":
            return wellbeing.stress_load(hub, now)
        if what == "ranges":
            return wellbeing.normal_ranges(hub, now)
        if what == "progress":
            return wellbeing.body_neutral_progress(hub, now)
        raise ValueError("what is macros, morning, weekly, bedtime, banking, load, ranges or progress")

    def schema(**props):
        return {"type": "object", "properties": {k: v for k, (v, _) in props.items()},
                "required": [k for k, (_, req) in props.items() if req]}

    string = {"type": "string"}
    return [
        Tool("week", "Training week: sets per muscle, new maxes, plateaus, adherence.", schema(), week,
             sensitive=True),
        Tool("lift", "One lift's estimated max, trend and next target (exercise).",
             schema(exercise=(string, True)), lift, sensitive=True),
        Tool("next", "Next session brief: targets, warm-up, time (optional title).",
             schema(title=(string, False)), next_session, sensitive=True),
        Tool("food", "Protein band, maintenance estimate, water, sleep.", schema(), food, sensitive=True),
        Tool("readiness", "Sleep-based readiness hint.", schema(), readiness, sensitive=True),
        Tool("plan", "Plan: what = programme (days, equipment), deload, goal (exercise, target kg, weeks), rescue "
                     "(missed).", schema(what=({"type": "string", "enum": ["programme", "deload", "goal", "rescue"]},
                                               True), days=({"type": "integer"}, False), equipment=(string, False),
                                         exercise=(string, False), target=(string, False), weeks=(string, False),
                                         missed=(string, False)), plan, sensitive=True),
        Tool("review", "Review: what = prs, consistency, balance, minutes, works, stall (exercise).",
             schema(what=({"type": "string", "enum": ["prs", "consistency", "balance", "minutes", "works", "stall"]},
                          True), exercise=(string, False)), review_, sensitive=True),
        Tool("injury", "Injury log: action = log, clear, list, ramp (area, note, id).",
             schema(action=({"type": "string", "enum": ["log", "clear", "list", "ramp"]}, True), area=(string, False),
                    note=(string, False), id=({"type": "integer"}, False)), injury, risk=1, sensitive=True),
        Tool("air", "Air quality before outdoor training.", schema(), air, sensitive=False),
        Tool("foods", "Your food list: action add (name, serving, kcal, protein, cost), list, adopt, remove, recipe "
                      "(name, items 'food x2; food').", schema(action=({"type": "string", "enum": ["add", "list", "adopt", "remove",
                                                                                   "recipe"]}, True),
                                               name=(string, False), serving=(string, False), kcal=(string, False),
                                               protein=(string, False), carbs=(string, False), fat=(string, False),
                                               vegetarian=({"type": "boolean"}, False), allergens=(string, False),
                                               cost=(string, False), items=(string, False)), foods_,
             risk=1, sensitive=True),
        Tool("eat_next", "Suggest foods from the user's own list: what = gap, vegetarian, cost (grams).",
             schema(what=({"type": "string", "enum": ["gap", "vegetarian", "cost"]}, False),
                    grams=(string, False)), eat_next, sensitive=True),
        Tool("wellbeing", "Food and recovery views: what = macros, morning, weekly, bedtime, banking, load, ranges, "
                          "progress.", schema(what=({"type": "string", "enum": [
                              "macros", "morning", "weekly", "bedtime", "banking", "load", "ranges", "progress"]}, True),
                                               days_until=({"type": "integer"}, False)), wellbeing_, sensitive=True),
    ]
