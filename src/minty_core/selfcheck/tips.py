"""A daily tip (683): one short line about something that is switched on (or a general one), the same all day, never repeated
within two weeks while there are others to show. Written by hand; no model call, nothing learned from your data."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from minty_core.fsutil import atomic_write_text

# (the switch that must be on, or None for any day, the tip)
TIPS: list[tuple[str | None, str]] = [
    (None, "The Timeline page in the panel lists every job I ran and every action I took, newest first."),
    (None, "The Switches page turns features on and off one at a time; everything new starts switched off."),
    (None, "The Prompts page lists the standing instructions I follow, and you can delete any of them."),
    (None, "The 'What I know' page shows every fact I keep about you and where it came from. Correct or erase any."),
    (None, "Say 'push to talk on' if you would rather I listened only while you hold the key."),
    (None, "I never type passwords or card numbers. If a page needs one, I wait while you type it yourself."),
    (None, "A plugin you make starts switched off. Read its plugin.py first, then turn it on."),
    (None, "Say 'stop' while I am talking and I stop."),
    (None, "Backups are encrypted with a passphrase only you know. Make one before big changes."),
    (None, "Nothing I read on a web page can give me orders. If a page tries to, I tell you."),
    (None, "The Brain page shows which AI providers are healthy and how much of each free allowance is left."),
    (None, "On a private day, what you say stays off providers that learn from it."),
    (None, "Ask me to explain my last action and I answer from its receipt, not from memory."),
    (None, "Set quiet hours and at night I only speak up for urgent things."),
    (None, "The panel works on your phone over your home Wi-Fi, and only there."),
    ("productivity.enabled", "Tick off a habit by saying 'I did my reading'. I keep the streak."),
    ("recorder.enabled", "Say 'start recording the lecture' and afterwards I make notes with a summary and action items."),
    ("butler.enabled", "Say 'what did you do for me today' for a plain list, with an undo for what can be undone."),
    ("study.enabled", "Say 'quiz me' for a spaced-repetition round of the cards you made."),
    ("study.enabled", "Ask me to plan your homework and I fit it around tuition and bedtime."),
    ("vault.enabled", "Say 'note that ...' to drop a thought into your notes inbox for sorting later."),
    ("telegram.enabled", "Send me a photo of a page with the caption 'summarise' and I pick out the key sentences."),
    ("web.helpers", "Paste a link and ask 'is this link safe' before you open it."),
    ("web.watch", "Ask me to watch a price or a page and I tell you when it changes, never faster than every few hours."),
    ("system.enabled", "Ask me to check for updates and I read the Arch news first and apply nothing."),
    ("dev.enabled", "Ask 'what does this command do' before you paste something into a terminal."),
    ("create.enabled", "Give me an outline and I make slides, a mind map or a document as new files."),
    ("integrations.calendar", "Ask 'what's next' and I read it from your calendar feed."),
    ("integrations.sleep_timer", "Say 'stop the music in 30 minutes' and the sound fades out and pauses."),
    ("selfcheck.weekly_report", "Every Sunday I can tell you what went wrong most often this week."),
]


def _enabled(cfg, flag: str | None) -> bool:
    if flag is None:
        return True
    node = cfg
    for part in flag.split("."):
        node = getattr(node, part, None)
        if node is None:
            return False
    return bool(node)


class TipBook:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("days", {})
        return data

    def pick(self, cfg, day: date) -> str:
        data = self._load()
        key = day.isoformat()
        if key in data["days"]:
            return data["days"][key]
        eligible = [t for flag, t in TIPS if _enabled(cfg, flag)]
        recent = [data["days"][k] for k in sorted(data["days"])[-14:]]
        fresh = [t for t in eligible if t not in recent] or sorted(eligible, key=lambda t: recent.index(t) if t in recent else -1)[:1]
        tip = fresh[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(fresh)]
        data["days"][key] = tip
        data["days"] = dict(sorted(data["days"].items())[-60:])
        atomic_write_text(self.path, json.dumps(data, indent=1, ensure_ascii=False), mode=0o600)
        return tip
