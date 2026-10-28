"""Recipes: a goal that runs by itself on a schedule.

A recipe is a standing instruction ("make today's reel and queue it for my approval") plus when to run it. At the time, it starts
a mission like any other: the same planning, checking, approvals and limits apply, and a recipe can do nothing a mission can't.
It is deliberately small:

* once a day (or `max_runs_per_day`), never two at once;
* a run missed because the computer was off is skipped and said so, never made up hours late;
* three failures in a row (`max_failures`) pause it, with the reason, until the user resumes it;
* it holds no permissions of its own. Whether something may be posted or sent without asking is a setting only the user edits.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.attention import Loop

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GROUPS = {"daily": DAY_NAMES, "weekdays": DAY_NAMES[:5], "weekends": DAY_NAMES[5:]}
MAX_GOAL = 1000
NOT_A_FAILURE = {"blocked", "cancelled", "interrupted"}


class RecipeError(ValueError):
    pass


@dataclass(frozen=True)
class Recipe:
    name: str
    goal: str
    at: str
    days: str
    max_runs_per_day: int
    max_failures: int
    failures: int
    paused: bool
    paused_reason: str
    paused_at: float
    created: float


@dataclass(frozen=True)
class Run:
    id: int
    recipe: str
    day: str
    slot: str  # scheduled | manual
    started: float
    finished: float | None
    status: str  # started | done | failed | blocked | timeout | cancelled | interrupted | skipped
    summary: str
    job_id: int | None


_RECIPE = "name, goal, at, days, max_runs, max_failures, failures, paused, paused_reason, paused_at, created"
_RUN = "id, recipe, day, slot, started, finished, status, summary, job_id"


def parse_days(text: str) -> tuple[str, ...]:
    text = (text or "daily").strip().lower()
    if text in GROUPS:
        return tuple(GROUPS[text])
    days = tuple(part.strip() for part in text.split(",") if part.strip())
    if not days or any(d not in DAY_NAMES for d in days):
        raise RecipeError("days must be daily, weekdays, weekends or a list like mon,wed,fri")
    return days


class Recipes:
    def __init__(self, path: Path, *, start: Callable[[str, dict], int | None], catch_up_minutes: int = 120,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._start, self._catch_up, self._clock = start, int(catch_up_minutes), clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS recipes (name TEXT PRIMARY KEY, goal TEXT NOT NULL, at TEXT NOT NULL, days TEXT NOT NULL,"
            " max_runs INTEGER NOT NULL, max_failures INTEGER NOT NULL, failures INTEGER NOT NULL DEFAULT 0,"
            " paused INTEGER NOT NULL DEFAULT 0, paused_reason TEXT NOT NULL DEFAULT '', paused_at REAL NOT NULL DEFAULT 0,"
            " created REAL NOT NULL)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, recipe TEXT NOT NULL, day TEXT NOT NULL,"
            " slot TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',"
            " job_id INTEGER)")
        # a run that was going when the program stopped is not going any more, and is not tried again today
        self._db.execute("UPDATE runs SET status='interrupted', finished=?, summary='Minty restarted while this was running'"
                         " WHERE status='started'", (self._clock(),))
        self._db.commit()

    # ---- the recipes ------------------------------------------------------------------------------
    def create(self, name: str, goal: str, at: str, days: str = "daily", max_runs_per_day: int = 1,
               max_failures: int = 3) -> Recipe:
        name, goal = str(name or "").strip(), " ".join(str(goal or "").split())
        if not NAME.fullmatch(name):
            raise RecipeError("the name must be lower-case letters, digits and dashes, up to 40 characters")
        if not goal or len(goal) > MAX_GOAL:
            raise RecipeError(f"say what it should do, in up to {MAX_GOAL} characters")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(at or "")):
            raise RecipeError("the time must be 24-hour HH:MM, for example 18:00")
        parse_days(days)
        if not 1 <= int(max_runs_per_day) <= 10 or not 1 <= int(max_failures) <= 20:
            raise RecipeError("max_runs_per_day is 1-10 and max_failures is 1-20")
        with self._lock:
            if self._row(name):
                raise RecipeError(f"a recipe called {name} already exists")
            self._db.execute("INSERT INTO recipes(name, goal, at, days, max_runs, max_failures, created) VALUES (?,?,?,?,?,?,?)",
                             (name, goal, at, str(days or "daily").lower(), int(max_runs_per_day), int(max_failures), self._clock()))
            self._db.commit()
        return self.get(name)

    def _row(self, name: str):
        return self._db.execute(f"SELECT {_RECIPE} FROM recipes WHERE name=?", (name,)).fetchone()

    @staticmethod
    def _recipe(r) -> Recipe:
        return Recipe(r[0], r[1], r[2], r[3], r[4], r[5], r[6], bool(r[7]), r[8], r[9], r[10])

    def get(self, name: str) -> Recipe | None:
        with self._lock:
            row = self._row(name)
        return self._recipe(row) if row else None

    def all(self) -> list[Recipe]:
        with self._lock:
            return [self._recipe(r) for r in self._db.execute(f"SELECT {_RECIPE} FROM recipes ORDER BY name")]

    def delete(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM recipes WHERE name=?", (name,))
            self._db.commit()
            return cur.rowcount == 1

    def pause(self, name: str, reason: str = "paused by you") -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE recipes SET paused=1, paused_reason=?, paused_at=? WHERE name=?",
                                   (reason[:300], self._clock(), name))
            self._db.commit()
            return cur.rowcount == 1

    def resume(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE recipes SET paused=0, paused_reason='', failures=0 WHERE name=?", (name,))
            self._db.commit()
            return cur.rowcount == 1

    # ---- the runs ---------------------------------------------------------------------------------
    @staticmethod
    def _run(r) -> Run:
        return Run(*r)

    def runs(self, name: str, limit: int = 10) -> list[Run]:
        with self._lock:
            rows = self._db.execute(f"SELECT {_RUN} FROM runs WHERE recipe=? ORDER BY id DESC LIMIT ?", (name, int(limit)))
            return [self._run(r) for r in rows.fetchall()]

    def _today(self, name: str, day: str) -> list[Run]:
        rows = self._db.execute(f"SELECT {_RUN} FROM runs WHERE recipe=? AND day=? ORDER BY id", (name, day)).fetchall()
        return [self._run(r) for r in rows]

    def tick(self, now: datetime | None = None) -> list[Run]:
        """Start what is due. Returns the runs started."""
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        started: list[Run] = []
        for recipe in self.all():
            if recipe.paused or DAY_NAMES[now.weekday()] not in parse_days(recipe.days):
                continue
            hour, minute = (int(x) for x in recipe.at.split(":"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < due:
                continue
            with self._lock:
                today = self._today(recipe.name, day)
                if any(r.status == "started" for r in today):
                    continue  # the last one is still going: never two at once
                if sum(r.status != "skipped" for r in today) >= recipe.max_runs_per_day or any(r.status == "skipped" for r in today):
                    continue
                if not today and (now - due).total_seconds() > self._catch_up * 60:
                    self._insert(recipe.name, day, "scheduled", "skipped", f"the computer was off at {recipe.at}; skipped, not made up late")
                    continue
            started.append(self._begin(recipe, day, "scheduled"))
        return [r for r in started if r is not None]

    def run_now(self, name: str) -> Run:
        recipe = self.get(name)
        if recipe is None:
            raise RecipeError(f"no recipe called {name}")
        with self._lock:
            if any(r.status == "started" for r in self._today(name, datetime.now().strftime("%Y-%m-%d"))):
                raise RecipeError(f"{name} is already running")
        return self._begin(recipe, datetime.now().strftime("%Y-%m-%d"), "manual")

    def _insert(self, name: str, day: str, slot: str, status: str, summary: str = "") -> int:
        now = self._clock()
        cur = self._db.execute("INSERT INTO runs(recipe, day, slot, started, finished, status, summary) VALUES (?,?,?,?,?,?,?)",
                               (name, day, slot, now, None if status == "started" else now, status, summary))
        self._db.commit()
        return cur.lastrowid

    def _begin(self, recipe: Recipe, day: str, slot: str) -> Run:
        with self._lock:
            run_id = self._insert(recipe.name, day, slot, "started")
        try:
            job_id = self._start(recipe.goal, {"recipe": recipe.name, "run": run_id})
        except Exception as e:  # noqa: BLE001 — a recipe that cannot start is a failed run, not a crash
            self.finished(run_id, "failed", f"could not start: {type(e).__name__}: {e}")
        else:
            with self._lock:
                self._db.execute("UPDATE runs SET job_id=? WHERE id=?", (job_id, run_id))
                self._db.commit()
        with self._lock:
            return self._run(self._db.execute(f"SELECT {_RUN} FROM runs WHERE id=?", (run_id,)).fetchone())

    def finished(self, run_id: int, status: str, summary: str = "") -> None:
        """A run's mission has ended. Failures in a row pause the recipe; a success clears them; a question is neither."""
        with self._lock:
            row = self._db.execute("SELECT recipe FROM runs WHERE id=?", (int(run_id),)).fetchone()
            if row is None:
                return
            self._db.execute("UPDATE runs SET status=?, summary=?, finished=? WHERE id=?",
                             (status, " ".join(str(summary).split())[:300], self._clock(), int(run_id)))
            name = row[0]
            if status == "done":
                self._db.execute("UPDATE recipes SET failures=0 WHERE name=?", (name,))
            elif status not in NOT_A_FAILURE:
                self._db.execute("UPDATE recipes SET failures=failures+1 WHERE name=?", (name,))
                recipe = self._row(name)
                if recipe and recipe[6] >= recipe[5] and not recipe[7]:
                    self._db.execute("UPDATE recipes SET paused=1, paused_reason=?, paused_at=? WHERE name=?",
                                     (f"it failed {recipe[6]} times in a row; the last time: {' '.join(str(summary).split())[:160]}",
                                      self._clock(), name))
            self._db.commit()

    def loops(self, now: float | None = None) -> list[Loop]:
        """Recipes that stopped themselves are something the user has to decide about."""
        return [Loop(f"recipe:{r.name}:paused", "recipe",
                     f"The recipe {r.name} paused itself: {r.paused_reason}. Say resume recipe {r.name} when it is sorted out.",
                     r.paused_at or (now or self._clock()), "today") for r in self.all() if r.paused and r.failures]
