"""Spoken commands that need no model: set a timer, a reminder, change the volume, pause the music. Each becomes one tool call
(through the agent's normal safety checks) and a short reply. Anything unclear returns None and the model handles it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from minty_core.fastpath import FastAction
from minty_core.instant.clock import spoken_time
from minty_core.instant.text import clean, parse_duration, say_duration, words_to_digits

MAX_TIMER_S = 2 * 86400
CATEGORY = "do it for me"


@dataclass(frozen=True)
class ActionRule:
    name: str
    category: str
    examples: tuple[str, ...]
    tool: str  # every example turns into a call of a tool whose name starts with this


def _timers_said(result: str) -> str:
    found = re.findall(r"Timer \d+ \((.*?)\): (\d+) seconds left", result)
    if not found:
        return result if result.strip() else "No timers running."
    parts = []
    for label, seconds in found:
        what = f"the {label} timer" if label and label != "timer" else "the timer"
        parts.append(f"{say_duration(int(seconds))} left on {what}")
    return (", and ".join(parts) if len(parts) > 1 else parts[0]).capitalize() + "."


_NOT_LABELS = {"a", "an", "the", "my", "new", "another", "countdown", "one"}
_SET_TIMER = [re.compile(p) for p in (
    r"^(?:set |start |make |create )?(?:an? )?(?:(?P<label>[a-z]+) )?(?:timer|countdown) (?:for |of |in )?(?P<d>.+)$",
    r"^(?:set |start |make |create )?(?:an? )?(?P<d>.+?) (?:timer|countdown)$",
    r"^(?:set |start )?(?:an? )?alarm in (?P<d>.+)$",
    r"^time me for (?P<d>.+)$",
)]
_TIMERS_LEFT = re.compile(r"^(?:how much time (?:is )?left(?: on (?:the|my) timer)?|how long (?:is )?left(?: on (?:the|my) timer)?|"
                          r"how long (?:until|till) (?:the|my) timer(?: goes off| ends| finishes| is done| rings)?|"
                          r"(?:what is|how is) (?:my |the )?timers?|list (?:my |the )?timers|any timers(?: running)?|"
                          r"check (?:my |the )?timers?|time left|how much time is left)$")
_VOLUME_UP = re.compile(r"^(?:turn |make )?(?:the )?(?:volume|sound|audio) up$|^volume up$|^turn it up$|^louder$|^increase (?:the )?volume$|"
                        r"^raise (?:the )?volume$|^turn up (?:the )?(?:volume|sound|audio)$|^make it louder$|^a bit louder$|^bump (?:the )?volume$")
_VOLUME_DOWN = re.compile(r"^(?:turn |make )?(?:the )?(?:volume|sound|audio) down$|^volume down$|^turn it down$|^quieter$|^softer$|"
                          r"^decrease (?:the )?volume$|^lower (?:the )?volume$|^turn down (?:the )?(?:volume|sound|audio)$|"
                          r"^make it quieter$|^a bit quieter$")
_VOLUME_SET = re.compile(r"^(?:set |put |change )?(?:the )?(?:volume|sound)(?: level)?(?: to| at)? (\d{1,3})(?: percent)?$|"
                         r"^(?:volume|sound) (?:to )?(max|maximum|full)$")
_MUTE = re.compile(r"^(?:mute|silence) (?:the )?(?:volume|sound|audio|speakers?|computer|system)$")
_UNMUTE = re.compile(r"^unmute (?:the )?(?:volume|sound|audio|speakers?|computer|system)$")
_BRIGHTNESS = re.compile(r"^(?:set |put |change )?(?:the )?(?:screen )?brightness(?: to| at)? (\d{1,3})(?: percent)?$")
_LOCK = re.compile(r"^lock (?:the |my )?(?:screen|computer|laptop|pc|session|machine)$")
_PAUSE = re.compile(r"^pause(?: the)?(?: music| song| track| playback| playing| spotify)?$")
_RESUME = re.compile(r"^(?:resume|unpause)(?: the)?(?: music| song| track| playback| playing| spotify)?$|^continue (?:the )?(?:music|song|track)$|"
                     r"^resume playing$")
_NEXT = re.compile(r"^(?:next (?:song|track)|skip|skip (?:this |the )?(?:song|track)|skip it|play the next (?:song|track)|"
                   r"go to the next (?:song|track))$")
_PREVIOUS = re.compile(r"^(?:previous (?:song|track)|go back a song|play the previous (?:song|track)|last song|"
                       r"go to the previous (?:song|track)|back a song)$")
_REMIND_IN_TAIL = re.compile(r"^remind me (?:to |about |that )?(.+) in (.+)$")
_REMIND_IN_HEAD = re.compile(r"^remind me in (.+?) (?:to|about|that) (.+)$")
_REMIND_AT_TAIL = re.compile(r"^remind me (?:to |about |that )?(.+) at (\d{1,2})(?::(\d{2}))? ?(am|pm)?$")
_REMIND_AT_HEAD = re.compile(r"^remind me at (\d{1,2})(?::(\d{2}))? ?(am|pm)? (?:to|about|that) (.+)$")


def _reminder_at(hour: int, minute: int, meridiem: str | None, now: datetime):
    """The next moment that clock time comes round -> (datetime) or None."""
    if minute > 59 or hour > 24:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    options = (hour % 24,) if meridiem or hour >= 12 or hour == 0 else (hour % 12, hour % 12 + 12)  # a bare 12 is noon
    best = None
    for h in options:
        target = now.replace(hour=h, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        if best is None or target < best:
            best = target
    return best


def _reminder(d: str, now: datetime) -> FastAction | None:
    m = _REMIND_IN_HEAD.match(d)
    if m and (seconds := parse_duration(m[1])):
        text = m[2]
    else:
        m = _REMIND_IN_TAIL.match(d)
        if m and (seconds := parse_duration(m[2])):
            text = m[1]
        else:
            seconds = text = None
    if seconds and text and 0 < seconds <= 365 * 86400:
        return FastAction("reminders_set", {"text": text, "in_minutes": round(seconds / 60, 3)},
                          f"Okay, I'll remind you in {say_duration(seconds)}.")
    m = _REMIND_AT_HEAD.match(d)
    if m:
        hour, minute, meridiem, text = int(m[1]), int(m[2] or 0), m[3], m[4]
    else:
        m = _REMIND_AT_TAIL.match(d)
        if not m:
            return None
        text, hour, minute, meridiem = m[1], int(m[2]), int(m[3] or 0), m[4]
    when = _reminder_at(hour, minute, meridiem, now)
    if when is None:
        return None
    return FastAction("reminders_set", {"text": text, "at": when.strftime("%Y-%m-%dT%H:%M")},
                      f"Okay, I'll remind you at {spoken_time(when)}.")


def match(text: str, wake_words=(), now: datetime | None = None) -> FastAction | None:
    cmd = clean(text, wake_words)
    if not cmd or len(cmd.split()) > 24:
        return None
    d = words_to_digits(cmd)
    if d.startswith("remind me"):
        return _reminder(d, now or datetime.now())
    for pattern in _SET_TIMER:
        m = pattern.match(d)
        if m:
            seconds = parse_duration(m["d"])
            if seconds and 0 < seconds <= MAX_TIMER_S:
                label = (m.groupdict().get("label") or "").strip()
                if label in _NOT_LABELS:
                    label = ""
                return FastAction("timers_set_timer", {"seconds": seconds, "label": label},
                                  f"Timer set for {say_duration(seconds)}.")
    if _TIMERS_LEFT.match(cmd):
        return FastAction("timers_list_timers", {}, _timers_said)
    if _VOLUME_UP.match(cmd):
        return FastAction("system_control", {"action": "volume", "value": "up"}, "Volume up.")
    if _VOLUME_DOWN.match(cmd):
        return FastAction("system_control", {"action": "volume", "value": "down"}, "Volume down.")
    m = _VOLUME_SET.match(d)
    if m:
        level = 100 if m[2] else int(m[1])
        if 0 <= level <= 100:
            return FastAction("system_control", {"action": "volume", "value": str(level)}, f"Volume at {level} percent.")
    if _MUTE.match(cmd):
        return FastAction("system_control", {"action": "mute"}, "Muted.")
    if _UNMUTE.match(cmd):
        return FastAction("system_control", {"action": "unmute"}, "Unmuted.")
    m = _BRIGHTNESS.match(d)
    if m and 5 <= int(m[1]) <= 100:
        return FastAction("system_control", {"action": "brightness", "value": m[1]}, f"Brightness at {int(m[1])} percent.")
    if _LOCK.match(cmd):
        return FastAction("system_control", {"action": "lock"}, "Locked.")
    if _PAUSE.match(cmd):
        return FastAction("spotify_pause", {}, "Paused.")
    if _RESUME.match(cmd):
        return FastAction("spotify_resume", {}, "Playing.")
    if _NEXT.match(cmd):
        return FastAction("spotify_next", {}, "Next.")
    if _PREVIOUS.match(cmd):
        return FastAction("spotify_previous", {}, "Going back.")
    return None


ACTIONS = (
    ActionRule("set a timer", CATEGORY, ("set a timer for 5 minutes", "timer for 90 seconds", "start a 10 minute timer",
                                         "set a pasta timer for twelve minutes", "5 minute timer", "set a timer for an hour and a half",
                                         "set a timer for half an hour", "set an alarm in 20 minutes"), "timers_set_timer"),
    ActionRule("how long is left on the timer", CATEGORY, ("how much time is left", "how long is left on the timer", "any timers running",
                                                           "list my timers"), "timers_list_timers"),
    ActionRule("set a reminder", CATEGORY, ("remind me in 10 minutes to call mom", "remind me to check the oven in 20 minutes",
                                            "remind me at 6 pm to take medicine", "remind me to stretch at 4:30 pm",
                                            "remind me in an hour to drink water"), "reminders_set"),
    ActionRule("volume", CATEGORY, ("volume up", "louder", "turn the volume down", "quieter", "set the volume to 40",
                                    "volume 60 percent", "mute the sound", "unmute the speakers", "turn it up", "volume to max"),
               "system_control"),
    ActionRule("screen", CATEGORY, ("set the brightness to 50", "brightness 70 percent", "lock the screen", "lock my laptop"),
               "system_control"),
    ActionRule("music controls", CATEGORY, ("pause", "pause the music", "resume the music", "unpause", "next song", "skip this song",
                                            "previous song", "skip", "go back a song", "resume"),
               "spotify_"),
)
