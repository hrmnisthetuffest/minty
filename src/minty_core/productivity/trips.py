"""Trip planner and packing lists (595): a list from a template for the kind of trip and the number of days."""
from __future__ import annotations

import time
from datetime import date

from minty_core.productivity.db import LifeDB

BASE = ["Phone and charger", "Wallet and some cash", "ID card", "Water bottle", "Earphones",
        "Toothbrush and toothpaste", "Tickets or booking details"]
EXTRA = {
    "hills": ["Warm jacket", "Gloves and cap", "Comfortable shoes"],
    "beach": ["Swimwear", "Sunscreen", "Sunglasses and hat", "Flip-flops", "Towel"],
    "city": ["Light bag", "Power bank", "Comfortable walking shoes"],
    "school trip": ["School ID", "Permission slip", "Packed lunch", "Notebook and pen"],
    "camping": ["Torch", "Sleeping bag", "Rain jacket", "Insect repellent"],
}
MAX_CLOTHES_DAYS = 7  # you can wash


def template(kind: str, days: int) -> list[str]:
    n = max(1, min(int(days), MAX_CLOTHES_DAYS))
    return [*BASE, f"{n} tops", f"{n} bottoms", f"{n + 1} pairs of socks and underwear", *EXTRA.get(kind.lower(), [])]


class Trips:
    def __init__(self, db: LifeDB):
        self.db = db

    def _row(self, words: str) -> dict:
        needle = " ".join(str(words).lower().split())
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM trips ORDER BY id").fetchall()]
        hits = [r for r in rows if needle and needle in r["name"].lower()]
        if not hits:
            raise ValueError("I don't have a trip like that.")
        return hits[0]

    def create(self, name: str, kind: str = "city", start: date | None = None, days: int = 3) -> int:
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("A trip needs a name.")
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO trips(name, kind, start, days, created, key) VALUES (?,?,?,?,?,?)",
                                     (name, kind.lower()[:20], start.isoformat() if start else "", int(days),
                                      time.time(), name.lower()))
            tid = cur.lastrowid
            for item in template(kind, days):
                self.db.db.execute("INSERT OR IGNORE INTO packing(trip_id, item) VALUES (?,?)", (tid, item))
            self.db.db.commit()
        return tid

    def list(self) -> list[dict]:
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM trips").fetchall()]
        return sorted(rows, key=lambda r: (r["start"] == "", r["start"], r["id"]))

    def items(self, words: str) -> list[dict]:
        t = self._row(words)
        with self.db.lock:
            return [{"item": r["item"], "done": bool(r["done"])} for r in self.db.db.execute(
                "SELECT item, done FROM packing WHERE trip_id=? ORDER BY id", (t["id"],)).fetchall()]

    def remaining(self, words: str) -> list[str]:
        return [i["item"] for i in self.items(words) if not i["done"]]

    def _set(self, words: str, item: str, done: int) -> str:
        t = self._row(words)
        needle = " ".join(item.lower().split())
        with self.db.lock:
            rows = self.db.db.execute("SELECT id, item FROM packing WHERE trip_id=?", (t["id"],)).fetchall()
            hit = next((r for r in rows if needle and needle in r["item"].lower()), None)
            if hit is None:
                raise ValueError("That is not on the list.")
            self.db.db.execute("UPDATE packing SET done=? WHERE id=?", (done, hit["id"]))
            self.db.db.commit()
        return hit["item"]

    def check(self, words: str, item: str) -> str:
        return self._set(words, item, 1)

    def uncheck(self, words: str, item: str) -> str:
        return self._set(words, item, 0)

    def add_item(self, words: str, item: str) -> None:
        t = self._row(words)
        item = " ".join(str(item).split())[:80]
        if not item:
            raise ValueError("What should I add?")
        with self.db.lock:
            self.db.db.execute("INSERT OR IGNORE INTO packing(trip_id, item) VALUES (?,?)", (t["id"], item))
            self.db.db.commit()

    def delete(self, words: str) -> bool:
        try:
            t = self._row(words)
        except ValueError:
            return False
        with self.db.lock:
            self.db.db.execute("DELETE FROM packing WHERE trip_id=?", (t["id"],))
            self.db.db.execute("DELETE FROM trips WHERE id=?", (t["id"],))
            self.db.db.commit()
        return True
