"""What Minty has told you by itself, and whether the Gmail watcher is working. Nothing here reads mail or sends anything."""
import time
from datetime import datetime

from minty_core.friction import DAY, analyze, render
from minty_core.plugins.host import Tool
from minty_core.policy import R1


def register(ctx):
    def notifier():
        return ctx.services.get("notifier")

    async def recent(args):
        n = notifier()
        if n is None:
            return "Notices are not running."
        rows = n.recent(int(args.get("limit") or 10))
        if not rows:
            return "Nothing has been told lately."
        return "\n".join(f"{r['severity']}: {r['title']} ({datetime.fromtimestamp(r['last_ts']).strftime('%a %H:%M')})"
                         f"{' — dealt with' if r['resolved'] else ''} [key {r['key']}]" for r in rows)

    async def resolve(args):
        n = notifier()
        key = str(args.get("key") or "")
        return f"Marked {key} as dealt with: no more reminders." if n is not None and n.resolve_by_key(key) else f"{key!r} is not one I told you about."

    async def status(args):
        watch = ctx.services.get("gmail_watch_store")
        cfg = ctx.services["config"]().gmail_watch
        if watch is None:
            return "The Gmail watcher is not set up."
        lines = [f"The Gmail watcher is {'on' if cfg.enabled else 'off'} (every {int(cfg.interval_s // 60)} minutes)."]
        for alias in cfg.accounts or watch.aliases():
            a = watch.account(alias)
            when = datetime.fromtimestamp(a["last_ok"]).strftime("%a %H:%M") if a["last_ok"] else "never"
            lines.append(f"{alias}: last checked {when}" + (f", failed {a['failures']} times ({a['last_error']})" if a["failures"] else ", no problems"))
        return "\n".join(lines)

    async def feedback(args):
        key, useful = str(args.get("key") or ""), bool(args.get("useful"))
        watch, prefs = ctx.services.get("gmail_watch_store"), ctx.services.get("prefs")
        sender = watch.sender_of(key) if watch is not None else ""
        if not sender or prefs is None:
            return "I do not know who that notice was about."
        kind, opposite = ("mail.vip", "mail.mute") if useful else ("mail.mute", "mail.vip")
        prefs.contradict(f"{opposite}:{sender}", "mail")  # what you just said undoes the opposite habit
        learned = prefs.observe(f"{kind}:{sender}", "mail", ref=key)
        if learned is None:
            return "Noted. I will not suggest that again for a while."
        if learned.status == "suggested":
            what = "treat mail from them as important" if useful else "stay quiet about mail from them"
            return f"I have noticed this a few times: should I {what}? It is a suggestion, not a rule: say yes (prefs_accept {learned.id}) or no (prefs_reject)."
        return f"Noted ({learned.evidence} so far). I will not change anything until I have seen it a few times."

    async def friction(args):
        approvals = ctx.services.get("approvals")
        if approvals is None:
            return "Questions are not being kept, so there is nothing to count."
        days = max(1, min(30, int(args.get("days") or 7)))
        get_tool = ctx.services.get("get_tool")
        has = (lambda tool: getattr(get_tool(tool), "mandate", None) is not None) if get_tool is not None else (lambda tool: False)
        now = time.time()
        jobs = ctx.services.get("jobs")
        done = sum(1 for j in jobs.select() if j.status == "done" and (j.finished or 0) >= now - days * DAY) if jobs is not None else 0
        return render(analyze(approvals.history(now - days * DAY), now=now, days=days, has_mandate=has, outcomes=done))

    s = {"type": "string"}
    return [
        Tool("recent", "What Minty told the user on its own lately (mail that mattered, failed jobs ...), with a key for each.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}}, recent),
        Tool("resolve", "The user dealt with a notice: stop reminding them about it (its key from notices_recent).",
             {"type": "object", "properties": {"key": s}, "required": ["key"]}, resolve, risk=R1),
        Tool("status", "Is the Gmail watcher on and working, per account.", {"type": "object", "properties": {}}, status),
        Tool("friction", "How much manual work is left: the questions the user answered lately, how long work waited for them, and which questions "
                         "could go. Only proposes.", {"type": "object", "properties": {"days": {"type": "integer"}}}, friction),
        Tool("feedback", "The user says a mail notice was useful or noise (key from notices_recent): learn slowly who matters. Only suggests.",
             {"type": "object", "properties": {"key": s, "useful": {"type": "boolean"}}, "required": ["key", "useful"]}, feedback, risk=R1),
    ]
