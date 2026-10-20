"""Plain-language permissions: what a tool can do, why Minty asks, and a weekly look at what is never used."""
from __future__ import annotations

import time

from minty_core.policy import ASK_FROM, classify

CLASS_SENTENCES = {
    0: "only looks things up and changes nothing",
    1: "makes small changes that are easy to undo",
    2: "does things other people can see, or that are hard to undo",
    3: "can spend money, use passwords or delete things",
}


def friendly(name: str) -> str:
    return name.replace("_", " ")


def describe_tool(tool, args: dict | None = None, mode: str = "commits") -> str:
    """One short paragraph a person can follow: what it does, how risky, whether and why Minty asks."""
    risk = classify(tool, args or {})
    parts = [f"{friendly(tool.name).capitalize()} {CLASS_SENTENCES[risk]}."]
    if getattr(tool, "egress", None) is not None:
        parts.append("It can send information to other servers.")
    if getattr(tool, "sensitive", False):
        parts.append("It handles private information, so it stays off providers that may train on it.")
    if risk >= 3:
        parts.append("I always ask first for this, whatever your settings are.")
    elif risk >= ASK_FROM.get(mode, 2):
        parts.append("I ask first because your safety mode is " + mode + ".")
    else:
        parts.append("I do not need to ask for this in " + mode + " mode.")
    return " ".join(parts)


def explain_named(host, name: str, mode: str = "commits") -> str:
    tool = host.get_tool(name)
    if tool is None:  # "send mail" for mail_send
        wanted = name.strip().lower().replace(" ", "_")
        tool = next((host.get_tool(s.name) for s in host.tool_specs() if wanted in s.name), None)
    if tool is None:
        return f"I don't have a tool called {name}."
    return describe_tool(tool, None, mode)


def explain_last(host, approvals, receipts, mode: str = "commits") -> str:
    """The permission behind the newest open question, else behind the newest change."""
    if approvals is not None:
        pending = approvals.pending()
        if pending:
            ap = pending[-1]
            tool = host.get_tool(ap.tool)
            reason = describe_tool(tool, None, mode) if tool is not None else ""
            return f"I asked: {ap.question} {reason}".strip()
    if receipts is not None:
        recent = receipts.recent(1)
        if recent:
            tool = host.get_tool(recent[0].tool)
            if tool is not None:
                return describe_tool(tool, None, mode)
    return "I have not asked for any permission lately."


def review(host, receipts, cfg_safety, *, days: int = 7, now: float | None = None) -> str:
    """What could change something, what actually did in the last `days`, and what looks safe to switch off."""
    now = now if now is not None else time.time()
    used = {r.tool for r in receipts.since(now - days * 86400)} if receipts is not None else set()
    can, never = [], []
    for spec in host.tool_specs():
        tool = host.get_tool(spec.name)
        if tool is None or classify(tool, {}) < 1:
            continue
        can.append(tool.name)
        if tool.name not in used:
            never.append(tool.name)
    if not can:
        return "Nothing I have can change anything, so there is nothing to review."
    parts = [f"This week I could change things with {len(can)} tools and used {len(used)} of them."]
    if never:
        names = ", ".join(friendly(n) for n in never[:6])
        parts.append(f"Never used: {names}" + (f" and {len(never) - 6} more" if len(never) > 6 else "")
                     + ". If you don't need them, switch their plugin off.")
    allows = [r for r in getattr(cfg_safety, "rules", []) if r.get("decision") == "allow"]
    if allows:
        parts.append(f"{len(allows)} of your rules let something run without asking.")
    if getattr(cfg_safety, "mode", "commits") == "trusted":
        parts.append("Your safety mode is trusted: I only ask for money, passwords and deleting.")
    return " ".join(parts)
