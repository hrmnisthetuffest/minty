"""Practice (722), the mistake log with targeted drills (723) and the weekly review (586)."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Callable

from minty_core.study.cards import Cards
from minty_core.study.db import StudyDB
from minty_core.study.plan import countdown

_Q = re.compile(r"^\s*(?:Q\.?\s*)?(\d{1,2})[.)]\s+(.*\S)\s*$")
_MARKS = re.compile(r"\s*[\[(]\s*(\d{1,2})\s*marks?\s*[\])]", re.I)
MIN_PER_MARK = 1.5
DAY = 86400


def _key(*parts: str) -> str:
    return "|".join(" ".join(p.lower().split()) for p in parts)


class Papers:
    def __init__(self, db: StudyDB, clock: Callable[[], float] = time.time):
        self.s, self._clock = db, clock

    def add(self, subject: str, topic: str, text: str, answer: str = "", marks: int = 0, source: str = "",
            year: str = "") -> int | None:
        text = " ".join(str(text).split())
        if not text:
            return None
        key = _key(subject.strip().title(), text)
        with self.s.lock:
            if self.s.db.execute("SELECT 1 FROM questions WHERE key=?", (key,)).fetchone():
                return None
            cur = self.s.db.execute(
                "INSERT INTO questions(subject, topic, text, answer, marks, source, year, key) VALUES (?,?,?,?,?,?,?,?)",
                (subject.strip().title(), topic.strip().lower(), text[:600], answer[:600], int(marks), source[:60],
                 year[:10], key))
            self.s.db.commit()
            return cur.lastrowid

    def import_text(self, subject: str, text: str, source: str = "", year: str = "") -> int:
        """A pasted paper: numbered questions, marks in brackets, indented lines continue the question."""
        items: list[list] = []
        for line in str(text).splitlines():
            m = _Q.match(line)
            if m:
                items.append([m.group(2), 0])
            elif items and line.strip() and not re.match(r"^\s*(?:section|part)\b", line, re.I):
                items[-1][0] += " " + line.strip()
        added = 0
        for body, _ in items:
            marks = 0
            mm = _MARKS.search(body)
            if mm:
                marks, body = int(mm.group(1)), (body[:mm.start()] + " " + body[mm.end():]).strip()
            if self.add(subject, "", body, marks=marks, source=source, year=year):
                added += 1
        return added

    def questions(self, subject: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM questions", []
        if subject:
            sql += " WHERE subject=?"
            args.append(subject.strip().title())
        with self.s.lock:
            return [dict(r) for r in self.s.db.execute(sql + " ORDER BY id", args).fetchall()]

    def practice(self, subject: str | None, minutes: int) -> list[dict]:
        pool = self.questions(subject)

        def weakness(q):  # 0 = never asked; otherwise the share answered wrongly
            return -1.0 if q["asked"] == 0 else 1.0 - q["correct"] / q["asked"]

        pool.sort(key=lambda q: (q["asked"] > 0, -weakness(q) if q["asked"] else 0, q["id"]))
        chosen, used = [], 0.0
        for q in pool:
            cost = max(q["marks"], 2) * MIN_PER_MARK
            if used + cost > minutes + MIN_PER_MARK and chosen:
                continue
            chosen.append(q)
            used += cost
            if used >= minutes:
                break
        return chosen

    def mark(self, ident: int, got_it: bool, now: float | None = None) -> None:
        with self.s.lock:
            self.s.db.execute("UPDATE questions SET asked=asked+1, correct=correct+?, last_ts=? WHERE id=?",
                              (1 if got_it else 0, self._clock() if now is None else now, ident))
            self.s.db.commit()

    def status(self, subject: str | None = None) -> dict:
        qs = self.questions(subject)
        return {"questions": len(qs), "asked": sum(1 for q in qs if q["asked"]),
                "correct": sum(q["correct"] for q in qs)}


class Mistakes:
    def __init__(self, db: StudyDB, cards: Cards, clock: Callable[[], float] = time.time):
        self.s, self.cards, self._clock = db, cards, clock

    def log(self, subject: str, topic: str, what: str, fix: str) -> int:
        subject, topic = subject.strip().title(), topic.strip().lower()
        what, fix = " ".join(what.split())[:200], " ".join(fix.split())[:200]
        if not what or not fix:
            raise ValueError("Say what went wrong and what the right answer is.")
        key = _key(subject, topic, what)
        now = self._clock()
        with self.s.lock:
            row = self.s.db.execute("SELECT id FROM mistakes WHERE key=?", (key,)).fetchone()
            if row:
                self.s.db.execute("UPDATE mistakes SET count=count+1, last_ts=? WHERE id=?", (now, row["id"]))
                self.s.db.commit()
                return row["id"]
        card = self.cards.add("mistakes", f"{subject}: correct this: {what}", fix, source=f"mistake:{topic}")
        with self.s.lock:
            cur = self.s.db.execute("INSERT INTO mistakes(subject, topic, what, fix, count, created, last_ts, card_id,"
                                    " key) VALUES (?,?,?,?,?,?,?,?,?)", (subject, topic, what, fix, 1, now, now, card,
                                                                         key))
            self.s.db.commit()
            return cur.lastrowid

    def list(self, subject: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM mistakes", []
        if subject:
            sql += " WHERE subject=?"
            args.append(subject.strip().title())
        with self.s.lock:
            return [dict(r) for r in self.s.db.execute(sql + " ORDER BY count DESC, last_ts DESC", args).fetchall()]

    def weak_topics(self, subject: str | None = None, limit: int = 5) -> list[dict]:
        totals: dict[tuple[str, str], dict] = {}
        for m in self.list(subject):
            t = totals.setdefault((m["subject"], m["topic"]), {"subject": m["subject"], "topic": m["topic"],
                                                              "count": 0, "last": 0.0})
            t["count"] += m["count"]
            t["last"] = max(t["last"], m["last_ts"] or 0)
        return sorted(totals.values(), key=lambda t: (-t["count"], -t["last"]))[:limit]

    def drill(self, subject: str | None = None, n: int = 5) -> list[dict]:
        ids = {m["card_id"] for m in self.list(subject) if m["card_id"]}
        due = [c for c in self.cards.due(self.cards._clock(), "mistakes", limit=200) if c["id"] in ids]
        fresh = []
        for i in sorted(ids):
            c = self.cards.get(i)
            if c and c["reps"] == 0:
                fresh.append(c)
        return (due + fresh)[:n]


def weekly_review(cards: Cards, papers: Papers, mistakes: Mistakes, exams, homework_open, receipts, today: date,
                  now: float) -> str:
    week_ago = now - 7 * DAY
    reviews = cards.reviews_between(week_ago, now + 1)
    with papers.s.lock:
        practised = papers.s.db.execute("SELECT COUNT(*) FROM questions WHERE last_ts>=?", (week_ago,)).fetchone()[0]
    with mistakes.s.lock:
        new_mistakes = mistakes.s.db.execute("SELECT COUNT(*) FROM mistakes WHERE created>=?", (week_ago,)).fetchone()[0]
    ticked = sum(1 for r in receipts if "school" in r.tool and "done" in r.tool)
    lines = [f"This week: {reviews} flashcard review{'s' * (reviews != 1)}, {practised} practice question"
             f"{'s' * (practised != 1)}, {new_mistakes} mistake{'s' * (new_mistakes != 1)} logged, {ticked} homework "
             f"item{'s' * (ticked != 1)} ticked off."]
    overdue = [h for h in homework_open if h.due and h.due < today]
    soon = [h for h in homework_open if h.due and today <= h.due <= today + timedelta(days=7)]
    if overdue:
        lines.append("Overdue: " + "; ".join(f"{h.subject}: {h.task}" for h in overdue[:4]) + ".")
    if soon:
        lines.append("Due this week: " + "; ".join(f"{h.subject}: {h.task} ({h.due:%a})" for h in soon[:5]) + ".")
    near = [e for e in exams if 0 <= (e[1] - today).days <= 14]
    if near:
        lines.append("Coming up: " + "; ".join(countdown(near, today)) + ".")
    weak = mistakes.weak_topics(limit=2)
    if weak:
        lines.append("Worth another look: " + ", ".join(f"{w['subject']} {w['topic']}" for w in weak) + ".")
    due = cards.stats()["due"]
    if due:
        lines.append(f"{due} flashcard{'s' * (due != 1)} waiting.")
    return "\n".join(lines)
