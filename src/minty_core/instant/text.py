"""Plain-text helpers for the instant answers: clean up what was said, read number words, say numbers."""
from __future__ import annotations

import math
import re

_LEAD_NAME = re.compile(r"^(?:(?:hey|hi|ok|okay|yo|oh|hello)\s+)?(?:{names})\b[\s,.!?:;-]*")
_FILLER = re.compile(
    r"^(?:(?:ok|okay|now|so|and|then|please|hey|um|uh|hmm|well(?! done)|just|actually|quick question|can you|could you|would you|"
    r"will you|can i get|do you know|i want to know|i would like to know|i need to know|i wanna know|tell me|let me know|"
    r"show me|give me)\b[\s,]*)+")
_TRAIL = re.compile(r"(?:^|[\s,]+)(?:please|for me|right now|now|thanks|thank you|if you can|real quick|quickly)[\s,]*$")
_CONTRACT = re.compile(r"\b(what|how|who|where|when|why)'?s\b")
_CONTRACT_IT = re.compile(r"\b(that|there|here|it)'s\b")


def _tidy(text: str) -> str:
    s = text.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"[\"“”]", " ", s)
    s = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", s)  # a.m. / p.m.
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)  # 1,200 -> 1200
    s = re.sub(r"(\d)\s*%", r"\1 percent", s)
    s = re.sub(r"[?!;]", " ", s)
    s = re.sub(r":(?!\d)", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)
    s = _CONTRACT.sub(r"\1 is", s)
    s = _CONTRACT_IT.sub(r"\1 is", s)
    return s


def _strip(s: str, wake_words) -> str:
    names = "|".join(re.escape(w.lower()) for w in wake_words if w)
    lead = re.compile(_LEAD_NAME.pattern.replace("{names}", names)) if names else None
    for _ in range(3):
        before = s
        s = " ".join(s.split())
        if lead is not None:
            s = lead.sub("", s)
        s = _FILLER.sub("", s)
        if s == before:
            break
    for _ in range(2):
        shorter = _TRAIL.sub("", s)
        if shorter.strip():  # "thanks" on its own is the request, not a trailing word
            s = shorter
    return " ".join(s.split())


def clean(text: str, wake_words=(), keep_commas: bool = False) -> str:
    """The request as a plain lower-case line: no wake word, polite fillers, trailing "please" or punctuation; "what's" is
    "what is"; "1,200" is "1200"; "5 p.m." is "5 pm". Commas are dropped unless asked for (lists need them)."""
    s = _tidy(text)
    if not keep_commas:
        s = s.replace(",", " ")
    else:
        s = re.sub(r"\s*,\s*", ", ", s)
    return _strip(s, wake_words).strip(" ,")


# ---------------------------------------------------------------------------------------------- number words
_SMALL = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen".split())}
_TENS = {w: 10 * n for n, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9}
_NUMBER_WORDS = set(_SMALL) | set(_TENS) | set(_SCALE) | {"hundred"}


def _number_at(tokens: list[str], i: int, bare_scale: bool = False) -> tuple[int, float | None]:
    """Read a number made of words starting at tokens[i]; returns (next index, value) or (i, None). bare_scale: "a hundred"."""
    j, total, current, seen = i, 0, 0, False
    while j < len(tokens):
        t = tokens[j]
        if t in _SMALL:
            v = _SMALL[t]
            if current == 0:
                current = v
            elif current % 100 == 0:
                current += v
            elif current % 10 == 0 and current % 100 >= 20 and v < 10:
                current += v
            else:
                break
        elif t in _TENS and current % 100 == 0:
            current += _TENS[t]
        elif t == "hundred" and (0 < current < 100 or (bare_scale and not seen)):
            current = current * 100 if current else 100
        elif t in _SCALE and (current > 0 or (bare_scale and not seen)):
            total += (current or 1) * _SCALE[t]
            current = 0
        elif (t == "and" and seen and (total or current) and current % 100 == 0 and j + 1 < len(tokens)
              and tokens[j + 1] in _NUMBER_WORDS):
            j += 1
            continue
        else:
            break
        seen, j = True, j + 1
    if not seen:
        return i, None
    value: float = total + current
    if j + 1 < len(tokens) and tokens[j] == "point" and tokens[j + 1] in _SMALL and _SMALL[tokens[j + 1]] < 10:
        digits, k = "", j + 1
        while k < len(tokens) and tokens[k] in _SMALL and _SMALL[tokens[k]] < 10:
            digits += str(_SMALL[tokens[k]])
            k += 1
        value, j = float(f"{int(value)}.{digits}"), k
    return j, value


def words_to_digits(text: str) -> str:
    """"twelve plus seven" -> "12 plus 7". "a hundred and five" -> "105", "minus five" -> "-5". Only whole runs of number words
    change; "a" and a lone "one" in ordinary speech are left to the rules that expect numbers."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "a" and i + 1 < len(tokens) and tokens[i + 1] in ("hundred", "thousand", "million", "billion"):
            j, value = _number_at(tokens, i + 1, bare_scale=True)
            if value is not None:
                out.append(num_plain(value))
                i = j
                continue
        sign = ""
        k = i
        after_number = bool(out) and re.fullmatch(r"-?\d+(?:\.\d+)?", out[-1]) is not None
        if t in ("minus", "negative") and i + 1 < len(tokens) and tokens[i + 1] in _NUMBER_WORDS and not (
                t == "minus" and after_number):  # "twelve minus five" is a subtraction, "minus five" is a number
            sign, k = "-", i + 1
        j, value = _number_at(tokens, k)
        if value is None:
            out.append(t)
            i += 1
        else:
            out.append(sign + num_plain(value))
            i = j
    return " ".join(out)


def num_plain(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


# ---------------------------------------------------------------------------------------------- saying numbers
def num(x: float) -> str:
    """A number as it is said: 1,200 for a thousand two hundred; 12.3457 for a long decimal; "1 times 10 to the 18" for huge."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return "not a number"
        if x.is_integer() and abs(x) < 1e15:
            x = int(x)
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e15 or (0 < a < 1e-6):
        exp = math.floor(math.log10(a))
        mant = f"{a / 10 ** exp:.3g}"
        return f"{sign}{mant} times 10 to the {exp}"
    if isinstance(x, int):
        return f"{x:,}"
    e = math.floor(math.log10(a)) if a else 0
    k = max(2, min(6, 5 - e))
    text = f"{a:,.{k}f}".rstrip("0").rstrip(".")
    return sign + (text or "0")


def plural(n: float, word: str) -> str:
    return f"{num(n)} {word}" if n == 1 else f"{num(n)} {word}s"


# ---------------------------------------------------------------------------------------------- durations
_UNIT_SECONDS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800}
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr|day|week)s?\b")


def parse_duration(text: str) -> float | None:
    """"5 minutes", "an hour and a half", "1 hour 30 minutes", "half an hour" -> seconds; None if it is anything else.
    Number words must already be digits (Query.digits)."""
    s = " ".join(text.lower().split())
    s = re.sub(r"\ban? (hour|minute|day|week|second) and a half\b", r"1.5 \1", s)
    s = re.sub(r"\b(?:half an?|a half) (hour|minute|day|week)\b", r"0.5 \1", s)
    s = re.sub(r"\bquarter of an? (hour|minute)\b", r"0.25 \1", s)
    s = re.sub(r"\b(?:an?|one) (hour|minute|day|week|second)\b", r"1 \1", s)
    pos, total = 0, 0.0
    for m in _PART.finditer(s):
        if s[pos:m.start()].strip(" ,") not in ("", "and"):
            return None
        total += float(m[1]) * _UNIT_SECONDS[m[2]]
        pos = m.end()
    if pos == 0 or s[pos:].strip(" ,"):
        return None
    return total


def say_duration(seconds: float) -> str:
    """90 -> "1 minute 30 seconds"; 5400 -> "1 hour 30 minutes"; 45 -> "45 seconds"."""
    total = int(round(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [plural(n, word) for n, word in ((days, "day"), (hours, "hour"), (minutes, "minute"), (secs, "second")) if n]
    if days or hours:
        parts = [p for p in parts if not p.endswith(("second", "seconds"))]
    return " ".join(parts[:2]) if parts else "0 seconds"
