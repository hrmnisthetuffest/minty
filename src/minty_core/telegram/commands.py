"""The phone's commands: quick capture, status, stop, lockdown, and a menu built from the skills.

Everything here calls the same local tools the voice would (journal, reminders, jobs, lockdown); nothing talks to a
model unless it is a skill, which is an ordinary request to the assistant.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

CORE_MENU = [("status", "How I am doing"), ("jobs", "What I'm working on"), ("where", "Which computer this is"),
             ("brief", "Today's brief"), ("note", "Save a note: /note text"), ("todo", "Add a to-do: /todo text"),
             ("idea", "Save an idea: /idea text"), ("remind", "/remind call dad in 20 minutes"),
             ("approvals", "What is waiting for your yes"), ("stop", "Stop everything I'm working on"),
             ("lockdown", "Stop everything and lock me down"), ("help", "What I can do")]
KEYBOARD = {"keyboard": [[{"text": "/status"}, {"text": "/jobs"}, {"text": "/brief"}],
                         [{"text": "/approvals"}, {"text": "/stop"}, {"text": "/where"}]],
            "resize_keyboard": True, "is_persistent": True}


def skill_command(name: str) -> str:
    """A Telegram command name (a-z, 0-9, _) for a skill name like 'shop-search'."""
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:32]


def install(gateway, *, host_call: Callable[[str, dict], Awaitable[str]], jobs=None, lockdown=None, brief=None,
            capacity=None, skills=None, remind: Callable[[str], Awaitable[str | None]] | None = None,
            state: Callable[[], str] = lambda: "running",
            vault_capture: Callable[[str, str], Awaitable[str | None]] | None = None,
            vault_find: Callable[[str], str] | None = None, standing=None,
            safe: Callable[[], str | None] | None = None) -> list[tuple[str, str]]:
    """Register the handlers on the gateway; returns the menu that was built (for setMyCommands)."""
    h = gateway.handlers

    async def note(chat_id: int, text: str, prefix: str = "", kind: str = "note") -> str:
        if not text:
            return "Say what to save, for example: /note buy a notebook"
        if vault_capture is not None:  # the notes folder when it is on; otherwise the journal below
            saved = await vault_capture(kind, text)
            if saved is not None:
                return saved
        out = await host_call("journal_add_entry", {"text": f"{prefix}{text}"})
        return "Saved." if not out.startswith("Error") else "I couldn't save that just now."

    async def _note(chat_id, args):
        return await note(chat_id, args)

    async def _todo(chat_id, args):
        return await note(chat_id, args, "To do: ", "todo")

    async def _idea(chat_id, args):
        return await note(chat_id, args, "Idea: ", "idea")

    async def _remind(chat_id, args):
        if not args:
            return "Say what and when, for example: /remind call dad in 20 minutes"
        if remind is None:
            return "I can't set reminders from here yet."
        reply = await remind(f"remind me to {args}")
        return reply or "I didn't catch when. Try: /remind call dad in 20 minutes, or at 6 pm."

    async def _status(chat_id, args):
        parts = [f"I'm {state()} on {gateway.machine}."]
        if jobs is not None:
            parts.append(jobs.summary())
        if capacity is not None:
            parts.append(capacity())
        return " ".join(parts)

    async def _jobs(chat_id, args):
        return jobs.summary() if jobs is not None else "I have no job list here."

    async def _where(chat_id, args):
        return f"This is Minty on {gateway.machine}. {state().capitalize()}."

    async def _stop(chat_id, args):
        if jobs is None:
            return "Nothing to stop."
        stopped = await jobs.cancel_all()
        return f"Stopped {len(stopped)} job{'s' * (len(stopped) != 1)}." if stopped else "Nothing was running."

    async def _lockdown(chat_id, args):
        if lockdown is None:
            return "Lockdown isn't available here."
        lockdown.engage("asked from Telegram")
        return "Lockdown. Everything is stopped and nothing changes until you lift it on the laptop."

    async def _brief(chat_id, args):
        return await brief() if brief is not None else "I have no brief to give."

    async def _help(chat_id, args):
        lines = [f"/{c} — {d}" for c, d in CORE_MENU]
        return "What I can do from here:\n" + "\n".join(lines) + "\nOr just write to me."

    for name, fn in (("note", _note), ("todo", _todo), ("idea", _idea), ("remind", _remind), ("status", _status),
                     ("jobs", _jobs), ("where", _where), ("stop", _stop), ("lockdown", _lockdown),
                     ("brief", _brief), ("help", _help)):
        h[f"/{name}"] = fn
    menu = list(CORE_MENU)
    if safe is not None:
        async def _safe(chat_id, args):
            return safe() or "There is no check-in open."

        h["/safe"] = _safe
        menu.append(("safe", "Close an open check-in: I'm fine"))
    if standing is not None:
        async def _standing(chat_id, args):
            """List or revoke only. A standing approval can be made at the laptop's terminal and nowhere else."""
            words = args.split()
            if words[:1] == ["revoke"] and len(words) == 2 and words[1].isdigit():
                return "Revoked." if standing.revoke(int(words[1])) else "There is no active one with that number."
            return standing.review() + "\n(/standing revoke N ends one. Making one needs the laptop's terminal.)"

        h["/standing"] = _standing
        menu.append(("standing", "Your standing approvals: /standing, /standing revoke N"))
    if vault_find is not None:
        async def _find(chat_id, args):
            return vault_find(args) if args.strip() else "Say what to look for, for example: /find ribbon cable"

        h["/find"] = _find
        menu.append(("find", "Where did I see that: /find words"))
    if skills is not None:
        for key, skill in sorted(skills.skills().items()):
            cmd = skill_command(key)
            if not cmd or f"/{cmd}" in h:
                continue

            async def _skill(chat_id, args, _key=key):
                await gateway.ask(chat_id, f"Use the {_key} skill. {args}".strip())
                return None

            h[f"/{cmd}"] = _skill
            menu.append((cmd, (getattr(skill, "description", "") or f"The {key} skill")[:200]))
    return menu
