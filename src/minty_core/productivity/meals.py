"""Meal plan and the shopping list it implies (596)."""
from __future__ import annotations

import json
from datetime import date, timedelta

from minty_core.productivity.db import LifeDB

SLOTS = ("breakfast", "lunch", "snack", "dinner")
COMMON = {
    "dal chawal": ["dal", "rice", "onion", "tomato", "turmeric"], "rajma chawal": ["rajma", "rice", "onion", "tomato"],
    "paratha": ["atta", "oil", "curd"], "khichdi": ["rice", "dal", "ghee"], "poha": ["poha", "onion", "peanuts"],
    "sandwich": ["bread", "butter", "cucumber"], "pasta": ["pasta", "tomato", "cheese"],
    "idli": ["idli batter", "chutney"], "omelette": ["eggs", "bread", "onion"], "chole": ["chickpeas", "onion", "tomato"],
}


def _ingredients(name: str, given: list[str] | None) -> list[str]:
    if given:
        return [" ".join(g.lower().split()) for g in given if g.strip()]
    low = name.lower()
    for key, items in COMMON.items():
        if key in low or all(w in low for w in key.split()):
            return list(items)
    return []


class Meals:
    def __init__(self, db: LifeDB):
        self.db = db

    def plan(self, day: date, slot: str, name: str, ingredients: list[str] | None = None) -> None:
        if slot not in SLOTS:
            raise ValueError("Meals are breakfast, lunch, snack or dinner.")
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("What is the meal?")
        with self.db.lock:
            self.db.db.execute("INSERT OR REPLACE INTO meals(day, slot, name, ingredients) VALUES (?,?,?,?)",
                               (day.isoformat(), slot, name, json.dumps(_ingredients(name, ingredients))))
            self.db.db.commit()

    def clear(self, day: date, slot: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM meals WHERE day=? AND slot=?", (day.isoformat(), slot)).rowcount
            self.db.db.commit()
        return bool(n)

    def week(self, start: date, days: int = 7) -> dict[str, dict[str, str]]:
        end = start + timedelta(days=days)
        with self.db.lock:
            rows = self.db.db.execute("SELECT day, slot, name FROM meals WHERE day>=? AND day<? ORDER BY day",
                                      (start.isoformat(), end.isoformat())).fetchall()
        out: dict[str, dict[str, str]] = {}
        for r in rows:
            out.setdefault(r["day"], {})[r["slot"]] = r["name"]
        return out

    def shopping_from_plan(self, start: date, days: int = 7) -> list[str]:
        end = start + timedelta(days=days)
        with self.db.lock:
            rows = self.db.db.execute("SELECT day, ingredients FROM meals WHERE day>=? AND day<?",
                                      (start.isoformat(), end.isoformat())).fetchall()
        wanted: list[str] = []
        for r in rows:
            for item in json.loads(r["ingredients"] or "[]"):
                if item not in wanted:
                    wanted.append(item)
        added = []
        with self.db.lock:
            for item in wanted:
                if self.db.db.execute("SELECT 1 FROM shopping WHERE item=?", (item,)).fetchone():
                    continue
                self.db.db.execute("INSERT INTO shopping(item, source) VALUES (?, 'plan')", (item,))
                added.append(item)
            self.db.db.commit()
        return added

    def add_item(self, item: str, qty: str = "") -> None:
        item = " ".join(str(item).lower().split())[:60]
        if not item:
            raise ValueError("What should I add?")
        with self.db.lock:
            self.db.db.execute("INSERT OR IGNORE INTO shopping(item, qty) VALUES (?,?)", (item, qty[:30]))
            self.db.db.commit()

    def check_item(self, words: str) -> str:
        needle = " ".join(str(words).lower().split())
        with self.db.lock:
            rows = self.db.db.execute("SELECT id, item FROM shopping").fetchall()
            hit = next((r for r in rows if needle and needle in r["item"]), None)
            if hit is None:
                raise ValueError("That is not on the list.")
            self.db.db.execute("UPDATE shopping SET done=1 WHERE id=?", (hit["id"],))
            self.db.db.commit()
        return hit["item"]

    def shopping(self, done: bool | None = None) -> list[dict]:
        sql, args = "SELECT item, qty, done FROM shopping", []
        if done is not None:
            sql += " WHERE done=?"
            args.append(1 if done else 0)
        with self.db.lock:
            return [{"item": r["item"], "qty": r["qty"], "done": bool(r["done"])}
                    for r in self.db.db.execute(sql + " ORDER BY id", args).fetchall()]

    def clear_done(self) -> int:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM shopping WHERE done=1").rowcount
            self.db.db.commit()
        return n
