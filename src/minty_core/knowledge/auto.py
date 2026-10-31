"""The second brain fills itself: what the owner says in a conversation is filed without being asked.

Only the owner's own words are ever filed, never Minty's answer, and only when they look like something worth keeping: a labelled line ("Idea:", "Lesson:"), a lesson or a guess or an idea
in their own phrasing ("I think ...", "what if we ..."), a decision, a plan or a fact about a project that mentions what a business or a project is made of (suppliers, prices,
customers, a launch), or a long message that is plainly thinking aloud. Not filed: commands and questions to Minty, chatter and acknowledgements, facts about the owner themselves
(memory already keeps those), anything that looks like a secret, anything that reads like orders to a model, forwarded messages (someone else's words), private conversations, and a turn that
already filed the same thing itself (`know_capture`, `memory_remember`). A ceiling per hour stops a long chat flooding the map.

This is a set of rules, not a model, on purpose: it costs nothing, adds no delay and is tested line by line. It will miss things (a thought that uses none of the cues); `know_capture` and the
panel's inbox are how those get in. What it files goes through the ordinary capture path, so it is split, linked, de-duplicated and marked `auto` so the owner can see where it came from.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from minty_core.knowledge.capture import _CUES, _DECISION, _LABEL, Capturer
from minty_core.memory import SECRET
from minty_core.provenance import looks_like_injection

log = logging.getLogger(__name__)

MIN_WORDS = 6
LONG_WORDS = 40
SKIP_TOOLS = ("know_", "memory_remember")  # a turn that did this itself has already filed it
_WORD = re.compile(r"[a-z0-9']+")
_COMMAND_START = frozenset("""play pause stop skip resume set turn open close send remind call text search find show tell give make create write draft add delete remove check read list start run launch
schedule book order buy navigate go get bring take put update change switch mute unmute volume lower raise increase decrease dim brighten google look translate calculate convert summarize
summarise explain research remember forget please hey hi hello thanks thank ok okay yes no yeah yep nope sure cool great nice good""".split())
_QUESTION_START = frozenset("what what's whats when where who who's whom whose why how how's which is are was were do does did can could would will shall should may might have has am".split())
_TO_MINTY = re.compile(r"\b(i want you to|i need you to|i'd like you to|i would like you to|you should|you need to|could you|can you|would you|will you|tell me|show me|give me|let me know)\b", re.I)
_PERSON = re.compile(r"^\s*(my|i'm|i am|i was|i have|i've|i live|i like|i love|i hate|i prefer|i usually|i always|i never)\b", re.I)
_PERSONAL_WORD = re.compile(r"\b(name|birthday|wife|husband|partner|girlfriend|boyfriend|son|daughter|mom|mum|dad|mother|father|brother|sister|dog|cat|address|phone|email|allerg\w*|favou?rite|born|live|lives|age|weigh|height)\b", re.I)
_CREDENTIAL_URL = re.compile(r"://[^/\s:@]+:[^@\s]+@")  # user:password@host
_NOTE_CUE = re.compile(r"^\s*(note that|for the record|fyi|heads up|worth noting|keep in mind)\b", re.I)
_IDEA_CUE = next(rx for kind, rx in _CUES if kind == "idea")
_WORK = frozenset("""launch launching launched supplier suppliers customer customers competitor competitors revenue margin margins pricing price prices invoice invoices strategy roadmap product products campaign
market distribution partner partners hiring budget forecast contract wholesale retail subscription roastery roaster batch batches shipment shipping freight inventory profit costs sales""".split())


@dataclass
class Verdict:
    why: str


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def judge(text: str) -> Verdict | None:
    """Is this message, said to Minty, something to keep? None when it is not."""
    text = str(text or "").strip()
    if not text or text.startswith("The user forwarded a message"):
        return None
    if looks_like_injection(text) or SECRET.search(text) or _CREDENTIAL_URL.search(text):
        return None
    labelled = sum(1 for ln in text.splitlines() if _LABEL.match(ln))
    if labelled:
        return Verdict("labelled")
    words = _words(text)
    if len(words) < MIN_WORDS:
        return None
    first = words[0]  # "we're" is not "were": the apostrophe stays
    asks = text.rstrip().endswith("?") or first in _QUESTION_START or first in _COMMAND_START or bool(_TO_MINTY.search(text))
    if _IDEA_CUE.search(text) and not _TO_MINTY.search(text):
        return Verdict("idea")  # "what if we ..." is a question in form and an idea in fact
    if asks:
        return None
    cue = next((kind for kind, rx in _CUES if rx.search(text)), "")
    if _PERSON.match(text) and _PERSONAL_WORD.search(text) and not cue:
        return None  # a fact about the owner: memory keeps those
    if cue:
        return Verdict(cue)
    if _DECISION.search(text):
        return Verdict("decision")
    if len(words) >= 8 and _NOTE_CUE.match(text):
        return Verdict("note cue")
    if len(words) >= 8 and len({w for w in words if w in _WORK}) >= 2:
        return Verdict("about the work")
    if len(words) >= LONG_WORDS:
        return Verdict("long")
    return None


class AutoCapture:
    """The hook the agent runs after every finished answer. Synchronous (the agent's hooks are); the filing itself is a background task that is kept and watched."""

    def __init__(self, store, capturer: Capturer, *, config: Callable, is_private: Callable[[], bool], history: Callable[[], list], scanner=None, clock: Callable[[], float] = time.time):
        self.store, self.capturer, self._config, self._private, self._history, self._scanner, self._clock = store, capturer, config, is_private, history, scanner, clock
        self._tasks: set[asyncio.Task] = set()

    def _done_by_tool(self) -> bool:
        """Did this very turn already call a tool that files or remembers it?"""
        for m in reversed(self._history()):
            if m.role == "user":
                return False
            if any(c.name.startswith(SKIP_TOOLS) for c in (m.tool_calls or [])):
                return True
        return False

    def observe(self, request: str, reply: str) -> None:
        cfg = self._config()
        if not cfg.auto_chat or self._private() or self._done_by_tool():
            return
        if judge(request) is None:
            return
        if self._scanner is not None and self._scanner.redact(request) != request:
            return  # something in it the scanner would hide: it does not go into a store
        if self.store.count_auto_captures(self._clock() - 3600) >= cfg.auto_per_hour:
            return
        cap = self.store.add_capture(request, channel="chat", origin="you", meta={"auto": True})
        try:
            task = asyncio.get_running_loop().create_task(self.capturer.process(cap["id"]))
        except RuntimeError:
            return  # no loop here: the capture waits in the inbox and the knowledge loop files it
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning("automatic capture failed", exc_info=task.exception())

    async def settle(self) -> None:
        """Wait for the filing in flight (tests, and a clean shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
