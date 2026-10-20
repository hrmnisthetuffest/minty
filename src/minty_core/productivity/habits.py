"""Habits with streaks (590). A streak counts consecutive expected days that were checked; today not being done yet
does not break it; days a habit is not expected (a weekday habit at the weekend) neither count nor break."""
from __future__ import annotations

from datetime import date, timedelta

from minty_core.productivity.db import LifeDB

LOOKBACK = 400


def _expected(every: str, d: date) -> bool:
    return every != "weekdays" or d.weekday() < 5


class Habits:
    def __init__(self, db: LifeDB):
        self.db = db

    def add(self, name: str, every: str = "daily") -> int:
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("A habit needs a name.")
        every = "weekdays" if every == "weekdays" else "daily"
        with self.db.lock:
            row = self.db.db.execute("SELECT id FROM habits WHERE key=?", (name.lower(),)).fetchone()
            if row:
                return row["id"]
            cur = self.db.db.execute("INSERT INTO habits(name, every, created, key) VALUES (?,?,?,?)",
                                     (name, every, date.today().isoformat(), name.lower()))
            self.db.db.commit()
            return cur.lastrowid

    def _find(self, words: str) -> dict:
        needle = " ".join(str(words).lower().split())
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM habits ORDER BY id").fetchall()]
        hits = [r for r in rows if needle and needle in r["name"].lower()]
        if not hits:
            raise ValueError("I don't have a habit like that.")
        exact = [r for r in hits if r["name"].lower() == needle]
        if len(hits) > 1 and not exact:
            raise ValueError("Which one: " + ", ".join(h["name"] for h in hits[:4]) + "?")
        return (exact or hits)[0]

    def check(self, words: str, day: date) -> str:
        h = self._find(words)
        with self.db.lock:
            self.db.db.execute("INSERT OR IGNORE INTO checkins(habit_id, day) VALUES (?,?)", (h["id"], day.isoformat()))
            self.db.db.commit()
        return h["name"]

    def remove(self, words: str) -> bool:
        try:
            h = self._find(words)
        except ValueError:
            return False
        with self.db.lock:
            self.db.db.execute("DELETE FROM checkins WHERE habit_id=?", (h["id"],))
            self.db.db.execute("DELETE FROM habits WHERE id=?", (h["id"],))
            self.db.db.commit()
        return True

    def _days(self, habit_id: int) -> set[date]:
        with self.db.lock:
            return {date.fromisoformat(r["day"]) for r in self.db.db.execute(
                "SELECT day FROM checkins WHERE habit_id=?", (habit_id,)).fetchall()}

    def status(self, today: date) -> list[dict]:
        with self.db.lock:
            habits = [dict(r) for r in self.db.db.execute("SELECT * FROM habits ORDER BY id").fetchall()]
        out = []
        for h in habits:
            days = self._days(h["id"])
            streak, d = 0, today
            for _ in range(LOOKBACK):
                if _expected(h["every"], d):
                    if d in days:
                        streak += 1
                    elif d != today:
                        break
                d -= timedelta(days=1)
            best = run = 0
            if days:
                d = min(days)
                while d <= max(max(days), today):
                    if _expected(h["every"], d):
                        run = run + 1 if d in days else 0
                        best = max(best, run)
                    d += timedelta(days=1)
            out.append({"id": h["id"], "name": h["name"], "every": h["every"], "streak": streak, "best": best,
                        "done_today": today in days})
        return out
