"""People notes (601) and occasions (592): consent-aware, local, deletable, with the derived items going with them."""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Callable

from minty_core.memory import SECRET
from minty_core.productivity.db import LifeDB
from minty_core.secretscan import mask_pii

SOURCES = ("told me", "my observation", "from them", "public")
_MD = re.compile(r"^(\d{2})-(\d{2})$")


def _md(text: str) -> str:
    m = _MD.match(str(text).strip())
    if not m:
        raise ValueError("Give the date as month-day, like 03-21.")
    try:
        date(2024, int(m.group(1)), int(m.group(2)))  # a leap year, so 02-29 is allowed
    except ValueError:
        raise ValueError("That is not a real date.") from None
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def _next(today: date, month: int, day: int) -> date:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:  # 29 Feb in a non-leap year: the 28th
            d = date(year, month, 28)
        if d >= today:
            return d
    return date(today.year + 1, month, day)


class People:
    def __init__(self, db: LifeDB, clock: Callable[[], float] = time.time):
        self.db, self._clock = db, clock
        self.on_delete: list[Callable[[str], None]] = []  # other stores drop what they derived from this person

    def _row(self, words: str) -> dict | None:
        needle = " ".join(str(words).lower().split())
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM people ORDER BY id").fetchall()]
        hits = [r for r in rows if needle and needle in r["name"].lower()]
        exact = [r for r in hits if r["name"].lower() == needle]
        if len(hits) > 1 and not exact:
            raise ValueError("Which one: " + ", ".join(h["name"] for h in hits[:4]) + "?")
        return (exact or hits or [None])[0]

    def add(self, name: str, birthday: str = "") -> int:
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("A person needs a name.")
        bd = _md(birthday) if birthday else ""
        with self.db.lock:
            row = self.db.db.execute("SELECT id FROM people WHERE key=?", (name.lower(),)).fetchone()
            if row:
                if bd:
                    self.db.db.execute("UPDATE people SET birthday=? WHERE id=?", (bd, row["id"]))
                    self.db.db.commit()
                return row["id"]
            cur = self.db.db.execute("INSERT INTO people(name, birthday, created, key) VALUES (?,?,?,?)",
                                     (name, bd, self._clock(), name.lower()))
            self.db.db.commit()
            return cur.lastrowid

    def add_fact(self, who: str, text: str, source: str) -> int:
        if source not in SOURCES:
            raise ValueError("Say whether they told you, or it is your own observation.")
        text = " ".join(str(text).split())[:200]
        if not text:
            raise ValueError("What should I note?")
        if mask_pii(text) != text or SECRET.search(text):
            raise ValueError("I don't keep ID numbers, phone numbers or passwords in notes about people.")
        row = self._row(who)
        if row is None:
            raise ValueError("I don't know that person yet.")
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO facts(person_id, text, source, created) VALUES (?,?,?,?)",
                                     (row["id"], text, source, self._clock()))
            self.db.db.commit()
            return cur.lastrowid

    def add_occasion(self, who: str, what: str, md: str) -> int:
        row = self._row(who)
        if row is None:
            raise ValueError("I don't know that person yet.")
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO occasions(person_id, who, what, md, created) VALUES (?,?,?,?,?)",
                                     (row["id"], row["name"], " ".join(what.split())[:60], _md(md), self._clock()))
            self.db.db.commit()
            return cur.lastrowid

    def get(self, who: str) -> dict | None:
        row = self._row(who)
        if row is None:
            return None
        with self.db.lock:
            facts = [dict(r) for r in self.db.db.execute(
                "SELECT text, source FROM facts WHERE person_id=? ORDER BY id", (row["id"],)).fetchall()]
        return {"name": row["name"], "birthday": row["birthday"], "facts": facts}

    def names(self) -> list[str]:
        with self.db.lock:
            return [r["name"] for r in self.db.db.execute("SELECT name FROM people ORDER BY name").fetchall()]

    def upcoming(self, today: date, within_days: int = 14) -> list[dict]:
        items = []
        with self.db.lock:
            people = [dict(r) for r in self.db.db.execute("SELECT * FROM people").fetchall()]
            occasions = [dict(r) for r in self.db.db.execute("SELECT * FROM occasions").fetchall()]
        for p in people:
            if p["birthday"]:
                items.append((p["name"], "Birthday", p["birthday"]))
        for o in occasions:
            items.append((o["who"], o["what"], o["md"]))
        out = []
        for who, what, md in items:
            month, day = int(md[:2]), int(md[3:])
            n = (_next(today, month, day) - today).days
            if n <= within_days:
                out.append({"who": who, "what": what, "in_days": n})
        return sorted(out, key=lambda o: (o["in_days"], o["who"], o["what"]))

    def delete(self, who: str) -> bool:
        row = self._row(who)
        if row is None:
            return False
        with self.db.lock:
            self.db.db.execute("DELETE FROM facts WHERE person_id=?", (row["id"],))
            self.db.db.execute("DELETE FROM occasions WHERE person_id=?", (row["id"],))
            self.db.db.execute("DELETE FROM people WHERE id=?", (row["id"],))
            self.db.db.commit()
        for hook in self.on_delete:
            try:
                hook(row["name"])
            except Exception:  # noqa: BLE001 — one store failing must not leave the rest behind
                pass
        return True
