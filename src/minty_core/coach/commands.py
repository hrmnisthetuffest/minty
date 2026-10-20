"""The coach's spoken controls: pause or resume coaching, and "why did you say that?" (answered from the advice log)."""
from __future__ import annotations

import re

_PAUSE = re.compile(r"^(?:please )?(?:pause|stop|hold) (?:the )?coaching$|^no more coaching$")
_RESUME = re.compile(r"^(?:please )?(?:resume|restart|start|unpause) (?:the )?coaching$|^coaching back on$")
_WHY = re.compile(r"^why did you say that$|^what (?:was|is) that based on$|^why (?:that|this) advice$|"
                  r"^what is that advice based on$|^where did that (?:advice|number|target) come from$")


class CoachCommands:
    def __init__(self, hub, enabled=lambda: True):
        self.hub, self._enabled = hub, enabled

    def handle(self, text: str) -> str | None:
        if not self._enabled():
            return None
        phrase = " ".join(re.sub(r"[?!.,']", " ", text.lower()).split())
        if _PAUSE.match(phrase):
            self.hub.set_profile("coach_paused", "1")
            return "Coaching is paused. I won't give training or food guidance until you say 'resume coaching'."
        if _RESUME.match(phrase):
            self.hub.set_profile("coach_paused", "0")
            return "Coaching is back on."
        if _WHY.match(phrase):
            last = self.hub.last_advice()
            if last is None:
                return "I haven't given you any coaching advice yet, so there's nothing to explain."
            rules = "; ".join(last["rules"]) or "no specific rule recorded"
            return (f"I said that ({last['topic']}) because of: {rules}. The numbers I had: {last['facts']} "
                    "It's guidance from your own data, not medical advice.")
        return None
