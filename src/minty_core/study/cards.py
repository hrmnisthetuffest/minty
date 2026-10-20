"""Flashcards on the FSRS scheduler (578): decks, due cards, new-card limit per day, spoken grading."""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable

from minty_core.study.db import StudyDB
from minty_core.study.fsrs import AGAIN, EASY, GOOD, HARD, CardState, Scheduler

_AGAIN = re.compile(r"\b(no|nope|wrong|forgot|forgotten|again|missed?|incorrect|don'?t know|do not know|didn'?t "
                    r"know|no idea|blank)\b", re.I)
_HARD = re.compile(r"\b(hard|barely|struggled?|struggling|difficult|hesitat\w*|close|nearly|unsure)\b", re.I)
_EASY = re.compile(r"\b(easy|obvious|trivial|effortless|too easy)\b", re.I)
_GOOD = re.compile(r"\b(good|got it|knew|know it|right|correct|yes|yep|yeah|remembered?|fine)\b", re.I)
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_FILLER = re.compile(r"^(?:it'?s|it is|the answer is|i think(?: it'?s| it is)?|maybe|probably)\s+", re.I)


def grade_from_text(text: str) -> int | None:
    for rx, grade in ((_AGAIN, AGAIN), (_EASY, EASY), (_HARD, HARD), (_GOOD, GOOD)):
        if rx.search(text or ""):
            return grade
    return None


def _numbers(words: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in _TENS or w in _UNITS or w == "hundred":
            total, j = 0, i
            while j < len(words) and (words[j] in _TENS or words[j] in _UNITS or words[j] in ("hundred", "and")):
                x = words[j]
                if x in _TENS:
                    total += _TENS[x]
                elif x in _UNITS:
                    total += _UNITS[x]
                elif x == "hundred":
                    total = (total or 1) * 100
                j += 1
            out.append(str(total))
            i = j
        else:
            out.append(w)
            i += 1
    return out


def normalise(text: str) -> str:
    t = _FILLER.sub("", " ".join(str(text).lower().replace("-", " ").split()))
    words = re.findall(r"[a-z0-9']+|[^\sa-z0-9']", t)
    words = [w for w in _numbers([w for w in words if re.match(r"[a-z0-9']", w)])]
    return " ".join(words).strip()


def _key(deck: str, front: str) -> str:
    return f"{deck.strip().lower()}|{' '.join(front.lower().split())}"


class Cards:
    def __init__(self, db: StudyDB, clock: Callable[[], float] = time.time, scheduler: Scheduler | None = None):
        self.s, self._clock, self.sched = db, clock, scheduler or Scheduler()

    def add(self, deck: str, front: str, back: str, source: str = "") -> int:
        front, back = " ".join(str(front).split()), " ".join(str(back).split())
        if not front or not back:
            raise ValueError("A card needs a question and an answer.")
        deck = " ".join(str(deck or "general").lower().split())[:30] or "general"
        with self.s.lock:
            row = self.s.db.execute("SELECT id FROM cards WHERE key=?", (_key(deck, front),)).fetchone()
            if row:
                return row["id"]
            cur = self.s.db.execute("INSERT INTO cards(deck, front, back, source, created, key) VALUES (?,?,?,?,?,?)",
                                    (deck, front[:300], back[:300], source[:60], self._clock(), _key(deck, front)))
            self.s.db.commit()
            return cur.lastrowid

    def get(self, ident: int) -> dict | None:
        with self.s.lock:
            r = self.s.db.execute("SELECT * FROM cards WHERE id=?", (ident,)).fetchone()
        return dict(r) if r else None

    def due(self, now: float | None = None, deck: str | None = None, limit: int = 50) -> list[dict]:
        now = self._clock() if now is None else now
        sql, args = "SELECT * FROM cards WHERE reps>0 AND due<=?", [now]
        if deck:
            sql += " AND deck=?"
            args.append(deck.lower())
        with self.s.lock:
            rows = self.s.db.execute(sql + " ORDER BY due, id LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def new_today(self, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.s.lock:
            return self.s.db.execute("SELECT COUNT(*) FROM reviews WHERE first=1 AND ts>=? AND ts<?",
                                     (day, day + 86400)).fetchone()[0]

    def next_card(self, now: float | None = None, deck: str | None = None, new_limit: int = 10) -> dict | None:
        now = self._clock() if now is None else now
        due = self.due(now, deck, 1)
        if due:
            return due[0]
        if self.new_today(now) >= new_limit:
            return None
        sql, args = "SELECT * FROM cards WHERE reps=0", []
        if deck:
            sql += " AND deck=?"
            args.append(deck.lower())
        with self.s.lock:
            r = self.s.db.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
        return dict(r) if r else None

    def review(self, ident: int, grade: int, now: float | None = None) -> dict:
        now = self._clock() if now is None else now
        card = self.get(ident)
        if card is None:
            raise ValueError("No such card.")
        prior = CardState(card["stability"], card["difficulty"], card["due"] or now, card["last_review"],
                          card["reps"], card["lapses"], card["state"]) if card["reps"] else None
        new = self.sched.review(prior, grade, now)
        with self.s.lock:
            self.s.db.execute("UPDATE cards SET stability=?, difficulty=?, due=?, last_review=?, reps=?, lapses=?,"
                              " state=? WHERE id=?", (new.stability, new.difficulty, new.due, new.last_review,
                                                      new.reps, new.lapses, new.state, ident))
            self.s.db.execute("INSERT INTO reviews(card_id, ts, grade, first) VALUES (?,?,?,?)",
                              (ident, now, grade, 1 if not card["reps"] else 0))
            self.s.db.commit()
        return self.get(ident)

    def check_answer(self, ident: int, said: str) -> tuple[int, bool]:
        card = self.get(ident)
        if card is None:
            raise ValueError("No such card.")
        want, got = normalise(card["back"]), normalise(said)
        ok = bool(got) and (got == want or (len(want.split()) <= 3 and set(want.split()) <= set(got.split())))
        return (GOOD, True) if ok else (AGAIN, False)

    def stats(self, deck: str | None = None) -> dict:
        where, args = (" WHERE deck=?", [deck.lower()]) if deck else ("", [])
        now = self._clock()
        with self.s.lock:
            total = self.s.db.execute(f"SELECT COUNT(*) FROM cards{where}", args).fetchone()[0]
            new = self.s.db.execute(f"SELECT COUNT(*) FROM cards{where}{' AND' if where else ' WHERE'} reps=0",
                                    args).fetchone()[0]
            due = self.s.db.execute(f"SELECT COUNT(*) FROM cards{where}{' AND' if where else ' WHERE'} reps>0 "
                                    "AND due<=?", (*args, now)).fetchone()[0]
        return {"total": total, "new": new, "due": due}

    def reviews_between(self, start: float, end: float) -> int:
        with self.s.lock:
            return self.s.db.execute("SELECT COUNT(*) FROM reviews WHERE ts>=? AND ts<?", (start, end)).fetchone()[0]
