"""The manual work that is left: how many questions you answered, how long work waited for you, and which of them were pointless.

Optimising the whole workflow means counting what the *user* still has to do, not only how fast a reply comes back. Every question the safety
gate asks is a record (approvals.py), so this reads them: questions a day, time work spent waiting for an answer, and per tool whether you
say yes every time (a question that can go), nobody answers (work that waited and gave up), or you usually say no (an action that is offered
too eagerly). It only ever proposes: a standing approval is something only you can add, and class 3 (money, keys, deletion, people) is
always asked by design and never proposed away. Nothing here changes a setting.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from minty_core.notifier import Event, clean

DAY = 86400.0
MIN_ASKS = 5  # fewer than this is a coincidence, not a pattern
WEEKLY_AT_LEAST = 10  # a quieter week gets no note
YES, NO, GONE = {"approved", "used"}, {"denied"}, {"expired"}


@dataclass(frozen=True)
class ToolFriction:
    tool: str
    asked: int
    approved: int
    denied: int
    expired: int
    risk: int
    wait_s: float


@dataclass(frozen=True)
class Friction:
    days: int
    asked: int
    answered: int
    per_day: float
    wait_s: float
    tools: list[ToolFriction] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    outcomes: int = 0  # jobs that finished in the window: what the questions bought


def analyze(records, *, now: float, days: int = 7, has_mandate: Callable[[str], bool] = lambda tool: False, outcomes: int = 0) -> Friction:
    since = now - days * DAY
    rows = [r for r in records if r.created >= since and r.status != "pending"]
    tools: dict[str, dict] = {}
    for r in rows:
        t = tools.setdefault(r.tool, {"asked": 0, "approved": 0, "denied": 0, "expired": 0, "risk": 0, "wait": 0.0})
        t["asked"] += 1
        t["risk"] = max(t["risk"], r.risk)
        if r.status in YES:
            t["approved"] += 1
        elif r.status in NO:
            t["denied"] += 1
        elif r.status in GONE:
            t["expired"] += 1
        if r.decided is not None and r.status not in GONE:
            t["wait"] += max(0.0, r.decided - r.created)
    listing = sorted((ToolFriction(name, v["asked"], v["approved"], v["denied"], v["expired"], v["risk"], v["wait"]) for name, v in tools.items()),
                     key=lambda t: -t.asked)
    answered = sum(t.approved + t.denied for t in listing)
    return Friction(days, len(rows), answered, len(rows) / days, sum(t.wait_s for t in listing), listing, _proposals(listing, has_mandate), outcomes)


def _proposals(tools: list[ToolFriction], has_mandate: Callable[[str], bool]) -> list[str]:
    out = []
    for t in tools:
        if t.asked >= MIN_ASKS and t.denied == 0 and t.expired == 0 and has_mandate(t.tool):
            out.append(f"{t.tool}: you said yes {t.approved} times and never said no. It already skips the question when you ask for it in your own words "
                       "(say the outcome you want, like \"research X\"), so these came up without you asking for it.")
        elif t.risk >= 3 and t.asked >= MIN_ASKS:
            out.append(f"{t.tool}: asked {t.asked} times. Money, keys, deletion and messages to people are always asked by design, so this will not change.")
        elif t.asked >= MIN_ASKS and t.denied == 0 and t.expired == 0:
            out.append(f"{t.tool}: you said yes {t.approved} times and never said no. A standing approval for exactly this would stop the question "
                       "(minty butler standing ...; only you can add one).")
        if t.expired >= 2:
            out.append(f"{t.tool}: {t.expired} questions went unanswered, so the work behind them waited and then gave up. Answer from Telegram, or turn the action off.")
        if t.denied >= 3 and t.denied > t.approved:
            out.append(f"{t.tool}: you usually say no ({t.denied} of {t.asked}). It is offered too eagerly; consider turning it off.")
    return out


def render(f: Friction) -> str:
    if f.asked == 0:
        return f"No questions in the last {f.days} days: nothing to fix."
    lines = [f"Last {f.days} days: {f.asked} questions ({f.per_day:.1f} a day), {f.answered} answered, work waited about {f.wait_s / 60:.0f} min for you."]
    if f.outcomes:
        lines.append(f"{f.outcomes} jobs finished in that time: {f.asked / f.outcomes:.1f} questions for each.")
    lines += [f"- {t.tool}: {t.asked} asked, {t.approved} yes, {t.denied} no, {t.expired} unanswered" for t in f.tools[:6]]
    lines += ["", *[f"* {p}" for p in f.proposals]] if f.proposals else ["Nothing to fix: no question here is a pattern you could remove."]
    lines.append("This report only proposes; nothing is changed.")
    return "\n".join(lines)


def weekly_event(f: Friction, week: str) -> Event | None:
    """One quiet note a week, only when there was a lot of asking and something that could change."""
    if f.asked < WEEKLY_AT_LEAST or not f.proposals:
        return None
    return Event("friction", f"week:{week}", "notable", f"You answered {f.answered} questions this week ({f.asked} asked)",
                 clean(" ".join(f.proposals[:2]), 300), "see notices_friction for all of it", fingerprint=week)


def friction_note(approvals, notifier, get_tool: Callable[[str], object], now: float | None = None) -> None:
    """The weekly note, through the notifier (which tells the same week's news once)."""
    now = time.time() if now is None else now
    f = analyze(approvals.history(now - 7 * DAY), now=now, days=7, has_mandate=lambda tool: getattr(get_tool(tool), "mandate", None) is not None)
    event = weekly_event(f, datetime.fromtimestamp(now).strftime("%G-W%V"))
    if event is not None:
        notifier.publish(event)
