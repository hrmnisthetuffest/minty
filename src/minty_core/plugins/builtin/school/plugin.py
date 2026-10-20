import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from minty_core.inbox import Inbox, parse_portal_lines
from minty_core.plugins.host import Tool
from minty_core.policy import R1, R2

log = logging.getLogger(__name__)

PORTAL_GOAL = (
    "Open {url} in the browser. The user logged in by hand earlier, so you should already be inside; if you see a "
    "login page, a password box or a CAPTCHA, stop and reply with exactly LOGIN_REQUIRED. Otherwise find the "
    "homework or assignments page and the notices or circulars page, and read "
    "them. Do not click anything that submits, sends, pays, uploads or changes data. Then reply with ONLY lines in "
    "these exact formats, one per line, nothing else:\n"
    "HOMEWORK | subject | the task | due date as YYYY-MM-DD, or none\n"
    "NOTICE | title | date as YYYY-MM-DD, or none | one short sentence\n"
    "Include only items that are still current."
)


def register(ctx):
    settings = ctx.settings or {}
    saved = ctx.config_dir / "school.json"
    path = str(ctx.config_dir / "inbox.db")
    held = ctx.state.get("inbox")  # kept across reloads: every settings change reloads the plugins
    if held is None or held[0] != path:
        if held is not None:
            held[1].close()
        held = (path, Inbox(ctx.config_dir / "inbox.db"))
        ctx.state["inbox"] = held
    inbox = held[1]

    def portal() -> str:
        url = str(settings.get("portal_url") or "").strip()
        if not url:
            try:
                url = str(json.loads(saved.read_text()).get("portal_url") or "")
            except (OSError, ValueError):
                url = ""
        return url

    def now() -> datetime:
        return ctx.services.get("now", datetime.now)()

    def words_due(due: date | None, today: date) -> str:
        if due is None:
            return "no due date"
        delta = (due - today).days
        if delta == 0:
            return "due today"
        if delta == 1:
            return "due tomorrow"
        if delta < 0:
            return f"was due {due:%A}"
        return f"due {due:%A}" if delta < 7 else f"due {due:%d %B}"

    def nudge_due_homework(current: datetime) -> int:
        """Homework due today or tomorrow becomes a nudge (the proactive policy decides when it is said)."""
        scheduler = ctx.services.get("scheduler")
        if scheduler is None:
            return 0
        added = 0
        for h in inbox.due_within(current.date(), 1):
            item = scheduler.add(f"{h.subject}: {h.task} is {words_due(h.due, current.date())}", current,
                                 user_set=False, kind="nudge", key=f"hw:{h.key}:{current.date()}")
            added += item is not None
        return added

    def store(parsed, current: datetime) -> str:
        new_hw = sum(inbox.add_homework(s, t, d, "portal", current)[1] for s, t, d in parsed.homework)
        new_notes = sum(inbox.add_notice(t, b, d, "portal", current)[1] for t, d, b in parsed.notices)
        nudge_due_homework(current)
        bits = []
        if new_hw:
            bits.append(f"{new_hw} new homework item{'s' * (new_hw != 1)}")
        if new_notes:
            bits.append(f"{new_notes} new notice{'s' * (new_notes != 1)}")
        text = "School check done: " + (", ".join(bits) if bits else "nothing new") + "."
        return text

    async def check_now(progress=lambda t: None, ask_user=None) -> str:
        url = portal()
        drive = ctx.services.get("browser_drive")
        if not url:
            return "Error: tell me your SchoolPad web address first (school_set_portal)."
        if drive is None:
            return "Error: the browser plugin is not available."
        reply = await drive(PORTAL_GOAL.format(url=url), progress, ask_user)
        if reply.startswith("Error:"):
            return reply
        parsed = parse_portal_lines(reply, now().date())
        if parsed.login_required and not (parsed.homework or parsed.notices):
            question = ("SchoolPad is asking for a login. Please log in once in the browser window, then ask me "
                        "to check school again.")
            if ask_user is not None:
                ask_user(question)
            return question
        return store(parsed, now())

    async def school_check(args):
        runner = ctx.services.get("jobs")
        if not portal():
            return "Error: tell me your SchoolPad web address first (school_set_portal)."
        if runner is None or args.get("wait") is True:
            return await check_now()

        async def work(job_ctx):
            result = await check_now(job_ctx.progress, job_ctx.ask_user)
            if result.startswith("Error:"):
                raise RuntimeError(result.removeprefix("Error:").strip())
            return result

        job = runner.submit("school", "check SchoolPad", work, budget_s=330, exclusive="browser")
        return f"On it, as school job {job.id}. I'll tell you what's new."

    async def set_portal(args):
        url = str(args.get("url") or "").strip()
        parts = urlsplit(url if "://" in url else "https://" + url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return "Error: give the web address of the SchoolPad login page, without a username or password."
        saved.write_text(json.dumps({"portal_url": parts.geturl()}))
        saved.chmod(0o600)
        return f"Saved. I'll check {parts.hostname} for school news."

    async def homework(args):
        items = inbox.open_homework()
        if not items:
            return "No homework left that I know of."
        today = now().date()
        return " ".join(f"{h.subject}: {h.task}, {words_due(h.due, today)}." for h in items[:12])

    async def done(args):
        words = str(args.get("words") or "")
        hits = inbox.mark_done(words, now())
        if not hits:
            return "I couldn't find open homework matching that."
        if len(hits) == 1 and hits[0].done:
            return f"Marked done: {hits[0].subject}, {hits[0].task}."
        return "Which one? " + "; ".join(f"{h.subject}: {h.task}" for h in hits[:5])

    async def add(args):
        subject, task = str(args.get("subject") or "").strip(), str(args.get("task") or "").strip()
        if not subject or not task:
            return "Error: homework needs a subject and a task."
        due = None
        if args.get("due"):
            try:
                due = date.fromisoformat(str(args["due"])[:10])
            except ValueError:
                return "Error: give the due date as YYYY-MM-DD."
        item, new = inbox.add_homework(subject[:40], task[:160], due, "you")
        nudge_due_homework(now())
        return (f"Added: {item.subject}, {item.task}, {words_due(item.due, now().date())}." if new
                else "That's already on the list.")

    async def notices(args):
        items = inbox.notices(unseen_only=args.get("all") is not True, mark_seen=True)
        if not items:
            return "No new notices."
        return " ".join(f"{n.title}" + (f" ({n.day:%d %B})" if n.day else "") + (f": {n.body}" if n.body else ".")
                        for n in items[:8])

    obj = {"type": "object", "properties": {}}

    return [
        Tool("check", "Check the SchoolPad portal now for homework and notices (runs in the "
             "background in the browser).", {"type": "object", "properties": {"wait": {"type": "boolean"}}},
             school_check, sensitive=True),
        Tool("set_portal", "Save the web address of the user's SchoolPad login page.",
             {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, set_portal,
             risk=R2, describe=lambda a: f"Should I use {str(a.get('url') or '')[:60]!r} as your school portal?"),
        Tool("homework", "List the homework still to do.", obj, homework, sensitive=True),
        Tool("done", "Mark a homework item done; words are any words from its subject or task.",
             {"type": "object", "properties": {"words": {"type": "string"}}, "required": ["words"]}, done,
             sensitive=True, risk=R1),
        Tool("add", "Add a homework item by hand (subject, task, optional due date YYYY-MM-DD).",
             {"type": "object", "properties": {"subject": {"type": "string"}, "task": {"type": "string"},
                                               "due": {"type": "string"}}, "required": ["subject", "task"]},
             add, sensitive=True, risk=R1),
        Tool("notices", "Read the school notices that are new (all=true for older ones too).",
             {"type": "object", "properties": {"all": {"type": "boolean"}}}, notices, sensitive=True),
    ]
