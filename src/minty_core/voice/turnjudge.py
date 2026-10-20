"""A small fast model watches the transcript after you pause and decides whether you have finished (answer now) or
are still thinking (wait for more). The word rules in turnhold.py catch "...and" and "um"; this catches what they
cannot: "so what I was thinking we could" or "my favourite is the one that".

It only ever *delays* (by at most audio.turn_judge_hold_ms, twice) and it is asked as little as possible: never for a
sentence the recogniser ended with a full stop or question mark, never for a short command, never in a private
moment. A slow, broken or unsure judge means "answer as usual".
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

from minty_core.llm.types import Message, TextDelta
from minty_core.voice.turnhold import unfinished

MIN_WORDS = 5  # fewer words than this is a command or a quick answer: just answer
SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")

JUDGE_SYSTEM = ("You watch a live voice conversation. The speaker just paused. Decide whether they have finished their "
                "turn or are about to continue. Reply with exactly one word: COMPLETE or INCOMPLETE. COMPLETE means a "
                "whole request, question, answer or statement, even a short one. INCOMPLETE means it stops in the "
                "middle of a thought, a list, a name, or a sentence that plainly needs an ending. If the assistant "
                "had just asked a question, a short answer to it is COMPLETE.")


def needs_judge(text: str) -> bool:
    """Only an unpunctuated thought of five words or more that the word rules call finished is worth a model call."""
    t = (text or "").strip()
    if not t or SENTENCE_END.search(t) or unfinished(t):
        return False
    return len(re.findall(r"[A-Za-z']+", t)) >= MIN_WORDS


async def judge_complete(ask: Callable[[str, str], Awaitable[str]], text: str, last_reply: str,
                         timeout_s: float) -> bool | None:
    """True: finished. False: still going. None: no answer in time, an error, or an answer that is neither."""
    user = (f"The assistant last said: {last_reply.strip()[:300]}\n" if last_reply.strip() else "") + \
        f"The speaker said: {text.strip()}"
    try:
        verdict = await asyncio.wait_for(ask(JUDGE_SYSTEM, user), timeout_s)
    except Exception:  # noqa: BLE001 — timeout or any failure: never hold a conversation up over this
        return None
    word = re.sub(r"[^a-z]", "", str(verdict or "").strip().lower())
    return True if word == "complete" else False if word == "incomplete" else None


def make_turn_judge(router, store, *, last_reply: Callable[[], str], private: Callable[[], bool]):
    """The coroutine the pipeline calls: text -> True / False / None. Asks a fast free provider (never a Claude
    process, which takes seconds) for one word, using the small model, with no tools."""

    def provider() -> str:
        cfg = store.config
        if cfg.audio.turn_judge_provider:
            return cfg.audio.turn_judge_provider
        return next((c.name for c in router.usable() if c.kind != "claude_cli" and c.tier == "fast"), "")

    async def ask(system: str, user: str) -> str:
        out = []
        async for event in router.stream([Message("system", system), Message("user", user)], [], tier="fast",
                                         prefer=provider() or None, size="small"):
            if isinstance(event, TextDelta):
                out.append(event.text)
        return "".join(out)

    async def judge(text: str) -> bool | None:
        try:
            if private():
                return None
            reply = last_reply()
        except Exception:  # noqa: BLE001
            return None
        return await judge_complete(ask, text, reply, store.config.audio.turn_judge_timeout_ms / 1000)

    return judge
