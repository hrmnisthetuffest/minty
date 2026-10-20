"""Voice phrases for alarms, habits, the day and the recorder. Whole-utterance matches only; anything that is not
clearly one of these goes on to the model untouched. Never raises: a problem here must not stop a normal request."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Callable

from minty_core.productivity.kit import ProductivityOff, ProductivityProvider
from minty_core.productivity.recorder import RecorderError

_TIME = re.compile(r"^(?P<h>\d{1,2})(?:[:. ]?(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)?$")
_ALARM = re.compile(r"^(?:(?:set|add|make)(?: me)?(?: an?)? alarm(?: for| at)|wake me(?: up)?(?: at| for)) "
                    r"(?P<t>.+?)(?: (?P<d>every day|daily|on weekdays|on weekends|weekdays|weekends))?$")
_HABIT = re.compile(r"^(?:i did|i've done|i have done|i just did|i just finished|i finished|check off|tick off)"
                    r"(?: my| the)? (?P<h>.+?)(?: today)?$")
_MARK_DONE = re.compile(r"^mark (?:my |the )?(?P<h>.+?) (?:as )?done$")
_START = re.compile(r"^(?:start|begin) recording(?: (?P<l>.+))?$|^record (?:this |the |my )(?P<l2>lecture|meeting|class|"
                    r"call|talk|interview|lesson|conversation)$")
_STOP = ("stop recording", "end the recording", "end recording", "stop the recording", "finish recording")
_MARK = ("mark this moment", "mark that moment", "mark this", "mark that")
_SNOOZE = ("snooze", "snooze the alarm", "snooze it", "five more minutes", "5 more minutes")
_STOP_ALARM = ("stop the alarm", "turn off the alarm", "alarm off", "turn the alarm off", "i'm up", "im up",
               "i am up", "dismiss the alarm")
_PLAN = ("my plan for today", "what's my plan for today", "whats my plan for today", "what's my plan",
         "plan my day", "what is my plan for today", "what's on today", "whats on today")
_SUMMARY = ("how was my day", "end of day summary", "my end of day summary", "summarise my day", "summarize my day",
            "sum up my day")
_WIND = ("wind down", "time to wind down", "start my wind down", "wind down routine", "start winding down")
_WORD_DAYS = {"weekdays": "weekdays", "on weekdays": "weekdays", "weekends": "weekends", "on weekends": "weekends"}


def _clock(text: str) -> str | None:
    m = _TIME.match(" ".join(text.lower().split()))
    if not m:
        return None
    h, mins, ap = int(m.group("h")), int(m.group("m") or 0), (m.group("ap") or "").replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    if not (0 <= h <= 23 and 0 <= mins <= 59):
        return None
    return f"{h:02d}:{mins:02d}"


class ProductivityCommands:
    def __init__(self, provider: ProductivityProvider, publish: Callable, today: Callable[[], date] = date.today,
                 now: Callable[[], datetime] = datetime.now):
        self._p, self._publish, self._today, self._now = provider, publish, today, now

    def handle(self, text: str) -> str | None:
        try:
            if not self._p.active():
                return None
            low = " ".join(str(text).lower().strip().rstrip("?.!").split())
            return self._recording(low) or self._alarm(low) or self._habit(low) or self._day(low)
        except (ProductivityOff, ValueError, RecorderError) as e:
            return str(e) or None
        except Exception:  # noqa: BLE001 — never stop a normal request
            return None

    # ---- the recorder ---------------------------------------------------------------------------------
    def _recording(self, low: str) -> str | None:
        m = _START.match(low)
        if not (m or low in _STOP or low in _MARK):
            return None
        if not self._p._config().recorder.enabled:
            return "The recorder is switched off (recorder.enabled on the Switches page)."
        rec = self._p.recorder()
        if m:
            label = (m.group("l") or m.group("l2") or "").strip()
            return self._p.start_recording(label, self._publish)
        if low in _STOP:
            return self._p.stop_recording(self._publish) if rec.recording else "I'm not recording."
        if not rec.recording:
            return "I'm not recording."
        at = rec.mark("")
        return f"Marked {int(at // 60)}:{int(at % 60):02d}."

    # ---- alarms ---------------------------------------------------------------------------------------
    def _alarm(self, low: str) -> str | None:
        m = _ALARM.match(low)
        if m:
            at = _clock(m.group("t"))
            if at is None:
                return None
            word = (m.group("d") or "").strip()
            days = _WORD_DAYS.get(word, "")
            repeat = bool(word)  # a bare "set an alarm for 6:30" rings once; "every day" / "weekdays" repeat
            self._p.life().alarms.add(at, days, "", self._p._config().productivity.alarm_brief, once=not repeat)
            return f"Alarm set for {at}" + (f", {days}." if days else ".")
        alarms = self._p.life().alarms if low in _SNOOZE + _STOP_ALARM else None
        if alarms is None:
            return None
        if low in _SNOOZE:
            if not alarms.ringing:
                return None  # not ours: nothing is ringing
            minutes = self._p._config().productivity.snooze_min
            alarms.snooze(self._now(), minutes)
            return f"Snoozed for {minutes} minutes."
        return "Stopped." if alarms.stop() else "Nothing is ringing."

    # ---- habits ---------------------------------------------------------------------------------------
    def _habit(self, low: str) -> str | None:
        m = _HABIT.match(low) or _MARK_DONE.match(low)
        if not m:
            return None
        habits = self._p.life().habits
        try:
            name = habits.check(m.group("h"), self._today())
        except ValueError as e:
            return str(e) if str(e).startswith("Which one") else None  # no such habit: normal conversation
        s = next(x for x in habits.status(self._today()) if x["name"] == name)
        return f"Ticked off {name}. Streak: {s['streak']} day{'s' * (s['streak'] != 1)}."

    # ---- the day --------------------------------------------------------------------------------------
    def _day(self, low: str) -> str | None:
        if low in _PLAN:
            return self._p.plan_text(self._today())
        if low in _SUMMARY:
            return self._p.summary_text(self._today())
        if low in _WIND:
            return self._p.wind_down_text(self._today())
        return None
