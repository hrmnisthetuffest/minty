"""What may be published without asking. The user writes this policy (in settings); nothing in Minty can change it.

Three answers: `auto` (the user switched automatic posting on, and everything about this post is clean), `ask` (the user sees the post
and decides) or `refuse` (it is broken or over a hard limit). Automatic is narrow on purpose: a video that passed its checks, made
only from files with a licence on record, a caption with no link, no @mention and no sensitive word, under a daily cap and a
minimum gap. By default every post is asked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DEFAULT_BLOCKED = ("password", "otp", "bank", "card number", "address", "phone number", "giveaway", "follow for follow", "dm me")
HARD_CAPTION = 2200  # Instagram's caption limit
HARD_HASHTAGS = 30
_URL = re.compile(r"https?://|www\.", re.I)
_MENTION = re.compile(r"(?<![\w.])@\w")
_HASHTAG = re.compile(r"(?<!\w)#\w")


@dataclass(frozen=True)
class PostPolicy:
    mode: str = "ask"  # ask | auto
    max_per_day: int = 1
    min_gap_hours: float = 6.0
    max_hashtags: int = 15
    blocked_words: tuple[str, ...] = DEFAULT_BLOCKED


@dataclass(frozen=True)
class PostFacts:
    kind: str
    caption: str
    problems: list = field(default_factory=list)  # from the video check
    licensed: bool = False


@dataclass
class Decision:
    mode: str
    reasons: list[str] = field(default_factory=list)


def decide(policy: PostPolicy, facts: PostFacts, auto_posted_today: int, hours_since_last: float | None) -> Decision:
    if facts.problems:
        return Decision("refuse", [f"the {facts.kind} failed its checks: " + "; ".join(facts.problems)])
    caption = facts.caption.strip()
    if not caption or len(caption) > HARD_CAPTION or len(_HASHTAG.findall(caption)) > HARD_HASHTAGS:
        return Decision("refuse", [f"the caption is empty, over {HARD_CAPTION} characters or has more than {HARD_HASHTAGS} hashtags"])
    reasons: list[str] = []
    if policy.mode != "auto":
        reasons.append("posting is set to ask first (you can switch on automatic posting in the studio settings)")
    if not facts.licensed:
        reasons.append("a file in it has no clean licence on record")
    if _URL.search(caption):
        reasons.append("the caption has a link")
    if _MENTION.search(caption):
        reasons.append("the caption mentions someone with @")
    if len(_HASHTAG.findall(caption)) > policy.max_hashtags:
        reasons.append(f"it has more than {policy.max_hashtags} hashtags")
    low = caption.lower()
    hit = next((w for w in policy.blocked_words if re.search(rf"\b{re.escape(w)}\b", low)), None)
    if hit:
        reasons.append(f"the caption touches something sensitive ({hit})")
    if auto_posted_today >= policy.max_per_day:
        reasons.append(f"you allow {policy.max_per_day} a day without asking and that many have gone")
    if hours_since_last is not None and hours_since_last < policy.min_gap_hours:
        reasons.append(f"the last post was only {hours_since_last:.0f} hours ago (the minimum gap is {policy.min_gap_hours:g} hours)")
    return Decision("ask" if reasons else "auto", reasons)
