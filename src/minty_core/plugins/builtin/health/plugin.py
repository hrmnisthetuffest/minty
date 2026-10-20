"""Health hub tools. What a model can read is a small facts pack (`health_summary`); raw rows are shown to you by
`health_show`, whose result never reaches a model (Tool.local_only). Deleting asks every time."""
from datetime import datetime, timezone

from minty_core.health import DOMAINS, TABLE_DOMAIN
from minty_core.health_quality import housekeeping_report, quality_report, summarize
from minty_core.plugins.host import Tool

MAX_ROWS = 12
DOMAIN_ENUM = {"type": "string", "enum": list(DOMAINS)}


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def register(ctx):
    def hub():
        h = ctx.services.get("health")
        if h is None:
            raise ValueError("the health store is switched off (health.hub)")
        return h

    def now() -> float:
        return hub()._clock()

    def summary(args):
        days = max(1, min(90, int(args.get("days") or 7)))
        return hub().facts_pack(days)

    def status(args):
        h = hub()
        counts, consent = h.counts(), h.consents()
        lines = [f"{d}: {'on' if consent[d] else 'off'}, {counts[d]} record{'s' * (counts[d] != 1)}" for d in DOMAINS]
        prof = ", ".join(f"{k}: {v}" for k, v in h.profile().items())
        return "; ".join(lines) + "." + (f" Profile: {prof}." if prof else " No profile yet.")

    def show(args):
        h, domain = hub(), str(args.get("domain") or "training")
        if domain not in DOMAINS:
            raise ValueError(f"domain must be one of: {', '.join(DOMAINS)}")
        since = now() - max(1, min(365, int(args.get("days") or 14))) * 86400
        lines: list[str] = []
        if domain == "training":
            sets: dict[int, list] = {}
            for s in h.rows("sets"):
                sets.setdefault(s["workout_id"], []).append(s)
            for w in sorted((w for w in h.rows("workouts") if w["started"] >= since), key=lambda w: -w["started"]):
                by: dict[str, list] = {}
                for s in sets.get(w["id"], []):
                    by.setdefault(s["exercise"], []).append(s)
                work = ", ".join(f"{ex} {len(ss)} sets, best {max((s['weight_kg'] or 0) for s in ss):g} kg"
                                 for ex, ss in list(by.items())[:5])
                mins = f"{round(w['duration_s'] / 60)} min" if w["duration_s"] else "?"
                lines.append(f"{_day(w['started'])} · {w['title']} · {mins} · {work or 'no sets'} ({w['origin']})")
        elif domain == "body":
            for r in sorted((r for r in h.rows("body") if r["ts"] >= since), key=lambda r: -r["ts"]):
                lines.append(f"{_day(r['ts'])} · {r['kind']} {r['value']:g} {r['unit']} ({r['origin']})")
        elif domain == "sleep":
            for r in sorted((r for r in h.rows("sleep") if r["start"] >= since), key=lambda r: -r["start"]):
                lines.append(f"{_day(r['start'])} · {r['minutes'] / 60:.1f} h ({r['origin']})")
        elif domain == "nutrition":
            floor = datetime.fromtimestamp(since).strftime("%Y-%m-%d")
            for r in sorted((r for r in h.rows("nutrition") if r["day"] >= floor), key=lambda r: r["day"],
                            reverse=True):
                lines.append(f"{r['day']} · {r['kcal'] or 0:g} kcal, {r['protein_g'] or 0:g} g protein "
                             f"({r['origin']})")
        else:
            for r in sorted((r for r in h.rows("activities") if r["started"] >= since), key=lambda r: -r["started"]):
                km = f"{(r['distance_m'] or 0) / 1000:.1f} km"
                mins = f"{round((r['duration_s'] or 0) / 60)} min"
                hr = f", avg HR {r['avg_hr']:g}" if r["avg_hr"] else ""
                lines.append(f"{_day(r['started'])} · {r['kind']} {km} {mins}{hr} ({r['origin']})")
        if not lines:
            return f"No {domain} records in that period."
        extra = f"\n… and {len(lines) - MAX_ROWS} more." if len(lines) > MAX_ROWS else ""
        return "\n".join(lines[:MAX_ROWS]) + extra

    def set_profile(args):
        h = hub()
        h.set_profile(str(args["key"]), str(args["value"]))
        return f"Saved. Your {args['key']} is {h.profile()[str(args['key'])]}."

    def consent(args):
        domain = str(args["domain"])
        if args.get("allow", True) is False:
            return hub().revoke(domain)
        hub().grant(domain)
        return f"OK, now collecting {domain} data. Say 'stop collecting my health data' any time."

    def delete(args):
        n = hub().delete_domain(str(args["domain"]))
        return f"Deleted {n} {args['domain']} record{'s' * (n != 1)}."

    def delete_describe(args):
        domain = str(args.get("domain"))
        try:
            n = hub().counts().get(domain, 0)
        except ValueError:
            n = 0
        return f"Delete all {n} {domain} records from your health data, for good"

    def check(args):
        if str(args.get("what") or "quality") == "hevy":
            issues = housekeeping_report(hub())
            if not issues:
                return "Nothing to tidy in your Hevy workouts."
            return "In your Hevy workouts: " + "; ".join(i.detail for i in issues) + ". I haven't changed anything."
        return summarize(quality_report(hub(), now()))

    def schema(**props):
        return {"type": "object", "properties": {k: v for k, (v, _) in props.items()},
                "required": [k for k, (_, req) in props.items() if req]}

    integer = {"type": "integer"}
    string = {"type": "string"}
    return [
        Tool("summary", "Facts pack of recent training, weight, sleep, food (days 1-90).",
             schema(days=(integer, False)), summary, sensitive=True),
        Tool("status", "Health domains on/off, record counts, profile.", schema(), status, sensitive=True),
        Tool("show", "Show the user their own records, for them only (domain, days). You do not see the list.",
             schema(domain=(DOMAIN_ENUM, False), days=(integer, False)), show, sensitive=True, local_only=True),
        Tool("set_profile", "Save a profile field: age, goals, equipment, constraints, units.",
             schema(key=({"type": "string", "enum": ["age", "goals", "equipment", "constraints", "units"]}, True),
                    value=(string, True)), set_profile, risk=1, sensitive=True),
        Tool("consent", "Start (allow true) or stop collecting a domain; only on the user's clear yes.",
             schema(domain=(DOMAIN_ENUM, True), allow=({"type": "boolean"}, False)), consent, risk=1),
        Tool("delete_data", "Delete every record of one domain. Always asks.",
             schema(domain=(DOMAIN_ENUM, True)), delete, risk=3, describe=delete_describe),
        Tool("check", "Check the data for gaps, duplicates, odd values (what: quality or hevy).",
             schema(what=({"type": "string", "enum": ["quality", "hevy"]}, False)), check, sensitive=True),
    ]
