"""What Minty has told you by itself, and whether the Gmail watcher is working. Nothing here reads mail or sends anything."""
from datetime import datetime

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

    s = {"type": "string"}
    return [
        Tool("recent", "What Minty told the user on its own lately (mail that mattered, failed jobs ...), with a key for each.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}}, recent),
        Tool("resolve", "The user dealt with a notice: stop reminding them about it (its key from notices_recent).",
             {"type": "object", "properties": {"key": s}, "required": ["key"]}, resolve, risk=R1),
        Tool("status", "Is the Gmail watcher on and working, per account.", {"type": "object", "properties": {}}, status),
    ]
