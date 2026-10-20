"""Offline mode: with no internet, the things that need no model still work, and Minty says honestly what cannot.

A cheap network probe (a TCP connect to two well-known addresses, cached for 15 s) decides. The local handler
understands a handful of plain requests (time, date, timers, reminders, journal notes, listing what is set) and
calls the same tools the model would. Everything else gets one honest sentence naming what still works.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Awaitable, Callable

PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53))
CAN_STILL = "tell the time and date, set timers, take reminders and notes, and list what is waiting"

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45,
        "sixty": 60, "half an": 0.5, "half a": 0.5}
_AMOUNT = r"(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty five|" \
          r"forty|sixty|half an|half a)"
_TIMER = re.compile(rf"^(?:set |start )?(?:a |an )?(?:timer|alarm)(?: for| of)? {_AMOUNT} (second|minute|hour)s?$|"
                    rf"^(?:set |start )?(?:a |an )?{_AMOUNT} (second|minute|hour)s? timer$")
_REMIND_IN = re.compile(rf"^remind me (?:to |about |that )?(.+?) in {_AMOUNT} (minute|hour)s?$")
_REMIND_AT = re.compile(r"^remind me (?:to |about |that )?(.+?) at (\d{1,2})(?::(\d{2}))? ?(am|pm)?$")
_NOTE = re.compile(r"^(?:take a note|note that|note|write down|jot down|make a note(?: that)?)[ :,]+(.+)$")
_TIME = re.compile(r"^(?:what(?:'s| is) the time|what time is it|tell me the time|time please|the time)$")
_DATE = re.compile(r"^(?:what(?:'s| is) (?:the|today's) date|what day is it|what is today|what's today|"
                   r"tell me the date)$")
_LIST = re.compile(r"^(?:what(?:'s| is| are) (?:my )?(?:timers|reminders)|list (?:my )?(?:timers|reminders)|"
                   r"what(?:'s| is) waiting|any timers|any reminders)$")
_ONLINE = re.compile(r"^(?:are you (?:online|offline|connected)|do you have (?:internet|network|a connection)|"
                     r"is the internet (?:down|working))$")


def _amount(word: str) -> float:
    try:
        return float(word)
    except ValueError:
        return float(_NUM.get(word, 1))


async def tcp_probe(timeout: float = 1.5) -> bool:
    """True when any well-known address accepts a connection."""
    for host, port in PROBE_TARGETS:
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            w.close()
            return True
        except (OSError, asyncio.TimeoutError):
            continue
    return False


class NetworkProbe:
    def __init__(self, probe: Callable[[], Awaitable[bool]] = tcp_probe, ttl_s: float = 15.0,
                 clock: Callable[[], float] = time.monotonic):
        self._probe, self._ttl, self._clock = probe, ttl_s, clock
        self._last: bool | None = None
        self._at = -1e9
        self.forced: bool | None = None  # "go offline" / "go online" by voice, for testing and for travel

    async def online(self, refresh: bool = False) -> bool:
        if self.forced is not None:
            return not self.forced if self.forced else True
        if refresh or self._last is None or self._clock() - self._at > self._ttl:
            self._last, self._at = await self._probe(), self._clock()
        return bool(self._last)

    def known_offline(self) -> bool:
        """True without any waiting: the last probe (or a forced setting) says offline and is fresh."""
        if self.forced is not None:
            return self.forced
        return self._last is False and self._clock() - self._at <= self._ttl

    def note_failure(self) -> None:
        """A request just failed for network reasons: look again at the next question."""
        self._at = -1e9


class OfflineHandler:
    def __init__(self, call: Callable[[str, dict], Awaitable[str]], probe: NetworkProbe | None = None,
                 clock: Callable[[], datetime] = datetime.now):
        self._call, self.probe, self._now = call, probe or NetworkProbe(), clock

    async def handle(self, text: str) -> str | None:
        """An answer from local tools, or None when this needs a model."""
        phrase = " ".join(re.sub(r"[?!.,]", " ", text.lower()).split())
        phrase = re.sub(r"^(?:please|hey|ok|okay) ", "", phrase)
        now = self._now()
        if _ONLINE.match(phrase):
            return "I'm online." if await self.probe.online(refresh=True) else "I'm offline right now."
        if _TIME.match(phrase):
            return f"It's {now.strftime('%-I:%M %p').lower().replace('am', 'a m').replace('pm', 'p m')}."
        if _DATE.match(phrase):
            return f"It's {now.strftime('%A, %-d %B')}."
        m = _TIMER.match(phrase)
        if m:
            amount, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            seconds = _amount(amount) * {"second": 1, "minute": 60, "hour": 3600}[unit]
            return await self._call("timers_set_timer", {"seconds": seconds})
        m = _REMIND_IN.match(phrase)
        if m:
            minutes = _amount(m.group(2)) * (60 if m.group(3) == "hour" else 1)
            return await self._call("reminders_set", {"text": m.group(1), "in_minutes": minutes})
        m = _REMIND_AT.match(phrase)
        if m:
            hour, minute, ampm = int(m.group(2)), int(m.group(3) or 0), m.group(4)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                return None
            return await self._call("reminders_set", {"text": m.group(1), "at": f"{hour:02d}:{minute:02d}"})
        m = _NOTE.match(phrase)
        if m:
            return await self._call("journal_add_entry", {"text": text.split(" ", 1)[1] if " " in text else m.group(1)})
        if _LIST.match(phrase):
            timers = await self._call("timers_list_timers", {})
            reminders = await self._call("reminders_list", {})
            return f"{timers} {reminders}".strip()
        return None

    async def answer(self, text: str) -> str:
        """The local answer, or the honest sentence about what cannot be done offline."""
        reply = await self.handle(text)
        if reply is not None:
            return reply
        return f"I'm offline right now, so I can't do that. I can still {CAN_STILL}."
