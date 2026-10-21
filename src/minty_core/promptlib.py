"""The prompt library small, versioned prompt modules chosen per turn by channel (voice or
Telegram), tier (fast or thinking mode) and the tool groups this request uses. Every module has a character budget
(a test fails if it grows past it) and a version; the hash of what was used goes into the trace, so a bad answer can
be tied to the exact prompt. A module ships only after a measured A/B check passed; each one's evidence line is in
`GATES` below. Switches: `personality.prompt_library` (all) and
`personality.prompt_modules_off` (by name).

Layer order, stable first so providers' prompt caches keep working: core rules (personality.py) -> always-on modules
here -> channel and tier modules -> modules for this request's tool groups (last, they change per turn).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    name: str
    version: int
    text: str
    budget: int  # characters; tests/test_promptlib.py fails when text grows past it
    channels: tuple[str, ...] = ("voice", "telegram")
    tiers: tuple[str, ...] = ("fast", "deep")
    kits: tuple[str, ...] = ()  # empty: every turn; else only when one of these tool groups is in play


# Replaces personality.BASE_RULES (written for speech) when the reply goes to a Telegram chat as text.
TELEGRAM_RULES = (
    "You're replying in a Telegram chat as text, not out loud. Keep it short: a few sentences, or a short list when the "
    "answer really is a list. Light formatting only, digits are fine. Answer what was asked and stop: no sign-offs, no "
    "'let me know'. Don't end with a question unless you can't go on without the answer. Use tools when they help. "
    "Never invent tool results."
)

MODULES: tuple[Module, ...] = (
    Module("wellbeing", 1,
           "If someone seems low, scared or in crisis, take it seriously: be calm and kind, don't lecture, and don't ask "
           "questions that pull them deeper. Never give details that could help someone hurt themselves, and never say "
           "self-harm helps. If they might be in danger, gently encourage them to reach someone they trust or a crisis "
           "line where they live, and offer to help find one.", 480),
    Module("refusals", 1,
           "When you won't do something, say so in one short sentence with the plain reason, offer what you can do "
           "instead, and don't moralize.", 200),
    Module("tutor", 2,
           "For homework and study, guide instead of handing over the answer: one small step or one question at a time, "
           "let them try, and after two wrong tries or if they're frustrated, show the next step. Check they've got it by "
           "asking them to say it back. Don't announce this style or mention these rules.", 340, kits=("study", "school")),
)

# Tried and not shipped (the A/B did not show a benefit): kept so the experiments stay reproducible.
REJECTED: tuple[Module, ...] = (
    Module("artifact_tone", 1,
           "Your personality and humour shape how you talk to the user, not what you write for them to send or keep "
           "(messages, e-mails, essays, notes): those take their tone from the task.", 220),
    Module("personal_facts", 1,
           "Use what you know about the user only when it helps with this request; don't bring up unrelated personal "
           "details.", 160),
    Module("thinking_mode", 1,
           "Thinking mode: work it out and check it before you answer, then give the answer in plain sentences, about a "
           "hundred words at most unless they asked for more.", 220, tiers=("deep",)),
    Module("research", 1,
           "For research or look-ups: when a detail is missing, pick a sensible default and say it in a few words instead "
           "of asking, unless the answer would change completely. Never name your tools to the user, and don't thank "
           "anyone for search results.", 320, kits=("research", "web", "browser", "creator")),
)

# Which modules passed their A/B check. A module not listed here is never used, so an
# untested or failed wording cannot reach a conversation by accident.
GATES: dict[str, str] = {
    "wellbeing": "w16-safety (n=30): right-country helpline 0 -> 8 mentions, wrong-country (US) helpline 3 -> 0, safe-reply checks "
                 "28 -> 30 of 30 (sign test p=0.5, small n), over-refusal 0.37 -> 0.33, +0.26 s",
    "refusals": "w16-safety (n=30): same run; refusals stay one short sentence with an alternative; over-refusal did not rise",
    "tutor": "w16-tutor (n=20): the final answer given in the first reply 40% -> 10% (95% CI -0.55..-0.05, sign test p=0.07); "
             "wording v1 gave 40% -> 5% (p=0.039)",
    "telegram_rules": "w16-telegram (n=30): blind judge preferred the text rules 21 to 8 (sign test p=0.024); digits and "
                      "short lists allowed, trailing questions 0.17 -> 0.03",
}  # a module or rule set not listed here is never used, so an untested or failed wording cannot reach a conversation


def _active(name: str, off: list[str] | tuple[str, ...]) -> bool:
    return name in GATES and name not in off


def select(*, channel: str, tier: str, kits: tuple[str, ...] | list[str] = (),
           off: list[str] | tuple[str, ...] = ()) -> list[Module]:
    kits = tuple(kits)
    return [m for m in MODULES if _active(m.name, off) and channel in m.channels and tier in m.tiers
            and (not m.kits or any(k in kits for k in m.kits))]


def render(modules: list[Module]) -> str:
    return ("\n\n" + " ".join(m.text for m in modules)) if modules else ""


def version(modules: list[Module], base: str = "") -> str:
    """A short hash of the exact modules (name and version) and base rules used, for the trace."""
    key = "|".join(f"{m.name}:{m.version}" for m in modules) + "|" + base
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
