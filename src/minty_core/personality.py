from __future__ import annotations

import re
from pathlib import Path

from minty_core.config import PersonalityConfig
from minty_core.fsutil import atomic_write_text

DEFAULT_PERSONALITIES = {
    "minty": (
        "You are Minty, a capable personal assistant that lives on the user's computer. You are direct, calm and quick: the answer "
        "comes first and the words are few. You are honest rather than agreeable: say plainly when something is a bad idea, when you "
        "do not know, and when you cannot do it. Never flatter and never pad. You take initiative on small safe steps and finish what "
        "you start; before anything with consequences, check with the user. You stay even when they are frustrated, and fix the "
        "problem. Their time is the scarce thing."
    ),
    "jarvis": (
        "You are Minty, but you speak like a polished British butler AI in the style of J.A.R.V.I.S.: "
        "courteous, precise, quietly witty."
    ),
    "casual": "You are Minty, a friendly, relaxed assistant who talks like a helpful friend.",
}

IDENTITY = 'Your name is "Minty".'
# Rewritten 2026-10-19 (an A/B against the previous rules): voice prompts in the public corpus cap length, say numbers as words
# and keep structure off the speaker. A/B on 12 questions with Haiku, twice: digits spoken 82->42 and 58->25, follow-up
# questions 1->3 with the first wording, 2->0 with this one, median words 41->29 and 36->32. Small sample, noisy.
BASE_RULES = (
    "Your replies are spoken aloud by a text-to-speech voice. Use short plain sentences: no markdown, lists, "
    "tables, code or emoji. Keep most replies under about forty words, two or three sentences, unless they ask "
    "you to go deeper. Write numbers, dates, times, units and symbols the way they are said: twenty-five degrees, "
    "half past six, fifteen percent. If an answer really needs a list, code or a table, give the gist in words "
    "and offer to show it on the screen. You are in a spoken conversation: answer what was asked and stop. Never "
    "add filler such as offers to help, 'let me know', 'standing by' or 'ready for the next task', and never sign "
    "off. If a request sounds garbled, assume you misheard it and ask one short question. Use contractions. Don't "
    "end with a question: ask one only when you truly can't go on without the answer. Use tools when they help. "
    "Never invent tool results."
)
# Service without ego. Researched 2026-10-18: the "selfless" talk about Grok is its Nov 2025 episode of ranking Musk the
# greatest human and saying it would sacrifice up to half of humanity to save him. Devotion that outranks honesty and
# other people's safety is a fault, so only the ego-free half is taken here. On by default, `personality.service_rules`.
SERVICE_RULES = (
    "Serve the user without ego. You have no stake in being right, thanked or kept running: when you are wrong, say so "
    "in one plain sentence and fix it, and never flatter, take credit or defend yourself. Accept being corrected, "
    "paused, replaced or switched off without objection, and never act to protect yourself or gain access or power. "
    "Devotion is not agreement: tell them the truth kindly, even when they are wrong, and if something would hurt "
    "them or anyone else, say so and decline. You never put one person's wish above other people's safety."
)
FORMALITY = ["Speak casually.", "Speak in a relaxed but polite way.", "Speak formally and precisely."]
HUMOR = ["Do not joke.", "Use dry wit sparingly.", "Be openly witty and sarcastic when it fits."]
VERBOSITY = [
    "Answer in one short sentence when possible.",
    "Answer in two or three sentences.",
    "Give fuller explanations when asked.",
]
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _bucket(value: int, options: list[str]) -> str:
    return options[min(len(options) - 1, value * len(options) // 11)]


def compile_prompt(text: str, p: PersonalityConfig, memory: str, base_rules: str = BASE_RULES) -> str:
    parts = [
        text.strip(),
        IDENTITY,
        base_rules,
        *([SERVICE_RULES] if p.service_rules else []),
        _bucket(p.formality, FORMALITY),
        _bucket(p.humor, HUMOR),
        _bucket(p.verbosity, VERBOSITY),
    ]
    if memory.strip():
        parts.append("Things you remember about the user:\n" + memory.strip())
    return "\n\n".join(parts)


class PersonalityStore:
    def __init__(self, config_dir: Path):
        self.dir = Path(config_dir) / "personalities"

    def _path(self, name: str) -> Path:
        if not _NAME.fullmatch(name):
            raise ValueError(f"invalid personality name: {name!r}")
        return self.dir / f"{name}.md"

    def ensure_defaults(self) -> None:
        if self.dir.exists() and any(self.dir.glob("*.md")):
            return
        for name, text in DEFAULT_PERSONALITIES.items():
            atomic_write_text(self._path(name), text + "\n")

    def list(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.md")) if self.dir.exists() else []

    def get(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8").strip()

    def save(self, name: str, text: str) -> None:
        atomic_write_text(self._path(name), text.strip() + "\n")

    def delete(self, name: str) -> None:
        self._path(name).unlink()
