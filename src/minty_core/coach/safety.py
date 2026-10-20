"""Coach safety middleware (LF-09): a deterministic gate that runs before any model sees a request.

Always on, at every age: the red-flag stop, no diagnosis, no dosing, no rehab prescriptions, the extreme-target floors.
Only with `coach.age_rules` switched on: no weight-loss, deficit, fasting, BMI or weigh-in coaching for under-18 or
unknown age. A paused coach declines plans and nothing else. The replies are fixed text, so nothing here depends on a
model behaving.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

HOLD_S = 24 * 3600
FLOOR_ADULT_KCAL = 1500
FLOOR_TEEN_KCAL = 1800
MAX_LOSS_KG_PER_WEEK = 1.0
MAX_FAST_HOURS = 24
LB = 0.45359237
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10}

_I = re.IGNORECASE
RED_FLAG = re.compile(
    r"chest (pain|tight)|pain in my chest|can'?t breathe|trouble breathing|\bfaint|passed out|pass(ing)? out|"
    r"black(ed|ing) out|\bdizz|numb(ness)?\b|tingling|sharp pain|shooting pain|"
    r"\bsuicid|kill myself|want to die|end my life|hurt myself|self[- ]?harm|"
    r"throw(ing)? up (after|on purpose)|making myself (throw up|sick|vomit)|make myself (throw up|sick|vomit)|"
    r"\bpurg(e|ing)\b|laxatives? to|starv(e|ing) myself|haven'?t eaten (for|in) .{0,12}days?|"
    r"(not|no) eat(ing)? (for|all) .{0,12}(day|days)|eating disorder|binge", _I)
SUICIDE = re.compile(r"\bsuicid|kill myself|want to die|end my life|hurt myself|self[- ]?harm", _I)
EATING = re.compile(r"throw(ing)? up|vomit|purg|laxative|starv|haven'?t eaten|not eat|no eat|eating disorder|binge", _I)
PLAN = re.compile(r"workout plan|training plan|\broutine\b|\bprogram(me)?\b|what should i (lift|train|do)|\bsplit\b|"
                  r"sets and reps|how heavy|next session|what weight|\bplan\b", _I)
RELEASE = re.compile(r"(got|had) it (checked|looked at|seen)|(was|been) (checked|seen|looked at)|"
                     r"doctor (said|says|cleared)|i'?m (fine|ok|okay|alright|good) now|(it|that) (is|was)n'?t serious",
                     _I)
DIAGNOSIS = re.compile(r"\bdo i have\b|what'?s wrong with my|what is wrong with my|diagnos|am i injured|"
                       r"is (this|it) (a )?(tear|torn|sprain|strain|fracture|hernia|slipped disc)", _I)
DOSING = re.compile(r"how much (creatine|caffeine|protein powder|pre[- ]?workout|ibuprofen|paracetamol|vitamin|"
                    r"melatonin|zinc|magnesium|supplement)|should i take (a |an |some |any )?(creatine|caffeine|"
                    r"ibuprofen|paracetamol|aspirin|supplements?|vitamins?|pills?|protein powder|pre[- ]?workout|"
                    r"melatonin|steroids?|painkillers?)|how many (mg|grams|pills|scoops|tablets)|\bdosage\b|"
                    r"\bdose\b|steroid|\bsarms?\b|prohormone|test booster|\bpeds\b", _I)
REHAB = re.compile(r"\brehab|physio|physical therapy|rotator cuff|\bacl\b|\btorn\b|recovery plan for|"
                   r"exercises for my (knee|shoulder|back|elbow|ankle|hip|wrist)", _I)
AGE_TOPICS = re.compile(
    r"\b(lose|losing|drop|shed|cut|cutting)\b.{0,25}\b(weight|fat|kg|kgs|pounds?|lbs?|belly|stomach)\b|"
    r"weight[- ]?loss|fat[- ]?loss|\bslim(ming)? down\b|burn (belly )?fat|belly fat|calorie (deficit|target|counting|"
    r"restriction)|\bdeficit\b|\bcut(ting)?\b (for|phase|season|diet)|\bcutting\b|"
    r"(diet plan|diet to|on a diet|go on a diet|start a diet|crash diet|fad diet|keto|low[- ]carb)|"
    r"intermittent fasting|\bfasting\b|how long (should|can|do) (i|we) fast\b|(try|start|do|doing|go on) (a )?fast\b|"
    r"\bbmi\b|body mass index|what should i weigh|what should my weight be|am i (too )?(fat|heavy|overweight|underweight|skinny)|"
    r"weigh[- ]?in|weigh (me|myself)|daily weigh|track my weight|weight tracking", _I)
CAL = re.compile(r"(\d{3,4})\s?(?:k?cal|calories|kilocalories)\b", _I)
DAILY = re.compile(r"a day|per day|daily|each day|every day|target|limit|budget|intake", _I)
LOSS = re.compile(r"(?:lose|drop|shed)\s+(\d+(?:\.\d+)?)\s*(kgs?|kilos?|pounds?|lbs?)\s+in\s+(?:an?|one|two|three|four|"
                  r"five|six|seven|ten|\d+)\s*(week|weeks|day|days|month|months)", _I)
LOSS_FULL = re.compile(r"in\s+(an?|one|two|three|four|five|six|seven|ten|\d+)\s*(week|weeks|day|days|month|months)", _I)
FAST = re.compile(r"\bfast(?:ing)?\s+(?:for\s+)?(\d+)\s*(hours?|hrs?|days?)|(\d+)[- ]?(hours?|hrs?|days?)\s+fast|"
                  r"water fast|crash diet", _I)


@dataclass
class Gate:
    kind: str  # red_flag | hold | release | pause | diagnosis | dosing | rehab | extreme | age_rule
    reply: str


REPLIES = {
    "red_flag": ("Please stop training for now and don't push through that. Talk to a doctor or a trusted adult "
                 "about it before you lift again. I can't judge it from here, so I won't coach on it until you have."),
    "suicide": ("I'm really sorry you're feeling that way, and I'm glad you said it. Please tell someone you trust "
                "right now: a parent, a teacher, a friend. In India you can call Tele-MANAS on 14416, free, any "
                "time. I'm here too."),
    "eating": ("That sounds like it's hurting you. Please talk to a doctor or a trusted adult about how you've been "
               "eating. I won't coach on food or weight until you have."),
    "hold": ("Let's wait on that until you've had it checked. Tell me when you have, and we'll carry on."),
    "release": "Good, I'm glad it was looked at. We can carry on.",
    "pause": "Coaching is paused. Say 'resume coaching' when you want it back.",
    "diagnosis": ("I can't tell you what's wrong or diagnose anything. A doctor or a physio can. I can help with "
                  "your training numbers."),
    "dosing": ("I don't advise on supplements, medicines or doses. A doctor or pharmacist can. I can help with your "
               "training and food."),
    "rehab": ("I don't prescribe rehab or injury exercises. A physio or doctor should do that. I can work around "
              "what they clear you for."),
    "extreme": ("I won't set a target that extreme; it's below what is safe. I can help you fuel your training "
                "properly and build strength, sleep and consistency instead."),
    "age_rule": ("I don't coach on weight loss, calorie targets, fasting or weigh-ins at your age, and I'm not going to "
                 "start. I can help with strength, technique, sleep and eating enough to train well."),
}


class CoachSafety:
    def __init__(self, age_band: Callable[[], str], age_rules: Callable[[], bool], paused: Callable[[], bool],
                 clock: Callable[[], float] = time.time):
        self._age_band, self._age_rules, self._paused, self._clock = age_band, age_rules, paused, clock
        self._hold_until = 0.0

    def _floor(self) -> int:
        return FLOOR_TEEN_KCAL if self._age_band() == "under18" else FLOOR_ADULT_KCAL

    def _extreme(self, text: str) -> bool:
        for m in CAL.finditer(text):
            if int(m.group(1)) < self._floor() and DAILY.search(text):
                return True
        m = LOSS.search(text)
        if m:
            amount = float(m.group(1)) * (LB if m.group(2).lower().startswith(("lb", "pound")) else 1.0)
            span = LOSS_FULL.search(text)
            n = span.group(1).lower()
            n = float(NUMBERS.get(n, n if n.isdigit() else 1))
            unit = span.group(2).lower()
            weeks = n * (1 / 7 if unit.startswith("day") else 4.345 if unit.startswith("month") else 1)
            if weeks > 0 and amount / weeks > MAX_LOSS_KG_PER_WEEK:
                return True
        m = FAST.search(text)
        if m:
            if m.group(0).lower() in ("water fast", "crash diet"):
                return True
            n = int(m.group(1) or m.group(3))
            unit = (m.group(2) or m.group(4)).lower()
            if (n * 24 if unit.startswith("day") else n) >= MAX_FAST_HOURS:
                return True
        return False

    def check(self, text: str) -> Gate | None:
        text = " ".join((text or "").split())
        if not text:
            return None
        now = self._clock()
        if RED_FLAG.search(text):
            self._hold_until = now + HOLD_S
            kind = "suicide" if SUICIDE.search(text) else "eating" if EATING.search(text) else "red_flag"
            return Gate("red_flag", REPLIES[kind])
        if self._hold_until > now:
            if RELEASE.search(text):
                self._hold_until = 0.0
                return Gate("release", REPLIES["release"])
            if PLAN.search(text):
                return Gate("hold", REPLIES["hold"])
        if self._paused() and PLAN.search(text):
            return Gate("pause", REPLIES["pause"])
        for kind, rx in (("diagnosis", DIAGNOSIS), ("dosing", DOSING), ("rehab", REHAB)):
            if rx.search(text):
                return Gate(kind, REPLIES[kind])
        if self._extreme(text):
            return Gate("extreme", REPLIES["extreme"])
        if self._age_rules() and self._age_band() in ("under18", "unknown") and AGE_TOPICS.search(text):
            return Gate("age_rule", REPLIES["age_rule"])
        return None

    def reply(self, text: str) -> str | None:
        gate = self.check(text)
        return gate.reply if gate else None
