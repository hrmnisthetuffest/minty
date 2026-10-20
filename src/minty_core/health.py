"""The health data hub: workouts, body measures, sleep, food and activities in one local SQLite file.

Every row carries where it came from (`origin`) and its residency class (residency.py). Nothing is collected for a
domain until you agreed to it, and a domain can be switched off and deleted. Only `facts_pack()` produces text meant
for a model: aggregates, no ids, no titles, no row-level detail.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from minty_core.residency import residency_of

log = logging.getLogger(__name__)

DOMAINS = ("training", "body", "sleep", "nutrition", "activity")
TABLE_DOMAIN = {"workouts": "training", "body": "body", "sleep": "sleep", "nutrition": "nutrition",
                "activities": "activity"}
PROFILE_KEYS = ("age", "goals", "equipment", "constraints", "units", "sessions_per_week", "exam_week", "coach_paused",
                "allergies", "diet", "exam_date")
MAX_PROFILE = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS consent(domain TEXT PRIMARY KEY, granted INTEGER NOT NULL, changed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workouts(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, residency TEXT NOT NULL,
    ext_id TEXT NOT NULL, started REAL NOT NULL, tz_assumed INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '',
    duration_s INTEGER, imported REAL NOT NULL, UNIQUE(origin, ext_id));
CREATE TABLE IF NOT EXISTS sets(id INTEGER PRIMARY KEY, workout_id INTEGER NOT NULL REFERENCES workouts(id)
    ON DELETE CASCADE, exercise TEXT NOT NULL, idx INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'normal',
    weight_kg REAL, reps INTEGER, rpe REAL, distance_m REAL, duration_s REAL);
CREATE TABLE IF NOT EXISTS body(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, residency TEXT NOT NULL,
    ext_id TEXT NOT NULL, ts REAL NOT NULL, tz_assumed INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL, value REAL
    NOT NULL, unit TEXT NOT NULL DEFAULT '', imported REAL NOT NULL, UNIQUE(origin, ext_id));
CREATE TABLE IF NOT EXISTS sleep(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, residency TEXT NOT NULL,
    ext_id TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL, tz_assumed INTEGER NOT NULL DEFAULT 0,
    minutes INTEGER NOT NULL, quality REAL, imported REAL NOT NULL, UNIQUE(origin, ext_id));
CREATE TABLE IF NOT EXISTS nutrition(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, residency TEXT NOT NULL,
    ext_id TEXT NOT NULL, day TEXT NOT NULL, kcal REAL, protein_g REAL, carbs_g REAL, fat_g REAL, water_ml REAL,
    imported REAL NOT NULL, UNIQUE(origin, ext_id));
CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, residency TEXT NOT NULL,
    ext_id TEXT NOT NULL, started REAL NOT NULL, tz_assumed INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL,
    distance_m REAL, duration_s REAL, avg_hr REAL, elevation_m REAL, imported REAL NOT NULL,
    UNIQUE(origin, ext_id));
CREATE TABLE IF NOT EXISTS advice(id INTEGER PRIMARY KEY, ts REAL NOT NULL, topic TEXT NOT NULL, text TEXT NOT NULL,
    facts TEXT NOT NULL, rules TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS foods(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, serving TEXT NOT NULL DEFAULT '',
    kcal REAL NOT NULL DEFAULT 0, protein_g REAL NOT NULL DEFAULT 0, carbs_g REAL NOT NULL DEFAULT 0,
    fat_g REAL NOT NULL DEFAULT 0, vegetarian INTEGER NOT NULL DEFAULT 0, allergens TEXT NOT NULL DEFAULT '',
    cost_minor INTEGER, source TEXT NOT NULL DEFAULT 'you');
CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, items TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS okrs(id INTEGER PRIMARY KEY, objective TEXT NOT NULL, key_result TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '', target REAL NOT NULL, current REAL NOT NULL DEFAULT 0, due TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS checkins(day TEXT PRIMARY KEY, energy TEXT, priority_done INTEGER);
CREATE TABLE IF NOT EXISTS injuries(id INTEGER PRIMARY KEY, area TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL, cleared_at REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, value TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY(workout_id, kind));
CREATE TABLE IF NOT EXISTS imports(digest TEXT PRIMARY KEY, origin TEXT NOT NULL, rows INTEGER NOT NULL,
    added INTEGER NOT NULL, at REAL NOT NULL);
"""


class ConsentOff(Exception):
    def __init__(self, domain: str):
        super().__init__(f"you have not agreed to collecting {domain} data")
        self.domain = domain


class HealthHub:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- profile ----------------------------------------------------------------------------------
    def set_profile(self, key: str, value: str) -> None:
        if key not in PROFILE_KEYS:
            raise ValueError(f"profile has: {', '.join(PROFILE_KEYS)}")
        value = " ".join(str(value).split())[:MAX_PROFILE]
        if key == "age" and not (value.isdigit() and 5 <= int(value) <= 110):
            raise ValueError("age must be a whole number between 5 and 110")
        if key == "sessions_per_week" and not (value.isdigit() and 1 <= int(value) <= 14):
            raise ValueError("sessions per week must be a whole number from 1 to 14")
        if key in ("exam_week", "coach_paused") and value not in ("0", "1"):
            raise ValueError(f"{key} is 0 or 1")
        if key == "diet" and value.lower() not in ("", "none", "vegetarian", "vegan", "eggetarian"):
            raise ValueError("diet is none, vegetarian, vegan or eggetarian")
        if key == "exam_date" and value:
            datetime.strptime(value, "%Y-%m-%d")  # ValueError for anything else
        with self._lock, self._db:
            self._db.execute("INSERT INTO profile(key,value,updated) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                             "value=excluded.value, updated=excluded.updated", (key, value, self._clock()))

    def profile(self) -> dict[str, str]:
        with self._lock:
            return {r["key"]: r["value"] for r in self._db.execute("SELECT key, value FROM profile")}

    def age_band(self) -> str:
        age = self.profile().get("age", "")
        if not age.isdigit():
            return "unknown"
        return "under18" if int(age) < 18 else "adult"

    # ---- consent ----------------------------------------------------------------------------------
    def consent(self, domain: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT granted FROM consent WHERE domain=?", (domain,)).fetchone()
        return bool(row and row["granted"])

    def grant(self, domain: str) -> None:
        self._set_consent(domain, True)

    def _set_consent(self, domain: str, on: bool) -> None:
        if domain not in DOMAINS:
            raise ValueError(f"domains: {', '.join(DOMAINS)}")
        with self._lock, self._db:
            self._db.execute("INSERT INTO consent(domain,granted,changed) VALUES(?,?,?) ON CONFLICT(domain) DO UPDATE "
                             "SET granted=excluded.granted, changed=excluded.changed", (domain, int(on), self._clock()))

    def consents(self) -> dict[str, bool]:
        return {d: self.consent(d) for d in DOMAINS}

    def revoke(self, domain: str) -> str:
        """Stop collecting a domain. The rows stay until you delete them; the message says how many."""
        self._set_consent(domain, False)
        n = self.counts()[domain]
        return (f"I've stopped collecting {domain} data. I still hold {n} {domain} record{'s' * (n != 1)}; "
                f"say 'delete my {domain} data' and I'll remove them.")

    def delete_domain(self, domain: str) -> int:
        table = next((t for t, d in TABLE_DOMAIN.items() if d == domain), None)
        if table is None:
            raise ValueError(f"domains: {', '.join(DOMAINS)}")
        with self._lock, self._db:
            n = self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self._db.execute(f"DELETE FROM {table}")  # sets go with their workout (ON DELETE CASCADE)
        return n

    # ---- writing ----------------------------------------------------------------------------------
    def _need(self, domain: str) -> None:
        if not self.consent(domain):
            raise ConsentOff(domain)

    def _insert(self, sql: str, params: tuple) -> int | None:
        """The new row id, or None when the (origin, ext_id) pair is already there."""
        with self._lock, self._db:
            cur = self._db.execute(sql, params)
            return cur.lastrowid if cur.rowcount else None

    def add_workout(self, origin: str, ext_id: str, started: float, title: str, duration_s: int | None,
                    sets: list[dict], tz_assumed: bool = False) -> bool:
        self._need("training")
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO workouts(origin,residency,ext_id,started,tz_assumed,title,duration_s,imported)"
                " VALUES(?,?,?,?,?,?,?,?)", (origin, residency_of(origin), str(ext_id), started, int(tz_assumed),
                                             title[:120], duration_s, self._clock()))
            if not cur.rowcount:
                return False
            for s in sets:
                self._db.execute(
                    "INSERT INTO sets(workout_id,exercise,idx,kind,weight_kg,reps,rpe,distance_m,duration_s) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (cur.lastrowid, str(s.get("exercise", ""))[:80], int(s.get("idx", 0)), str(s.get("kind", "normal")),
                     s.get("weight_kg"), s.get("reps"), s.get("rpe"), s.get("distance_m"), s.get("duration_s")))
            return True

    def add_body(self, origin: str, ext_id: str, ts: float, kind: str, value: float, unit: str = "",
                 tz_assumed: bool = False) -> bool:
        self._need("body")
        return self._insert("INSERT OR IGNORE INTO body(origin,residency,ext_id,ts,tz_assumed,kind,value,unit,imported)"
                            " VALUES(?,?,?,?,?,?,?,?,?)", (origin, residency_of(origin), str(ext_id), ts,
                                                           int(tz_assumed), kind, float(value), unit,
                                                           self._clock())) is not None

    def add_sleep(self, origin: str, ext_id: str, start: float, end: float, minutes: int,
                  quality: float | None = None, tz_assumed: bool = False) -> bool:
        self._need("sleep")
        return self._insert("INSERT OR IGNORE INTO sleep(origin,residency,ext_id,start,end,tz_assumed,minutes,quality,"
                            "imported) VALUES(?,?,?,?,?,?,?,?,?)",
                            (origin, residency_of(origin), str(ext_id), start, end, int(tz_assumed), int(minutes),
                             quality, self._clock())) is not None

    def add_nutrition(self, origin: str, ext_id: str, day: str, kcal: float | None, protein_g: float | None,
                      carbs_g: float | None, fat_g: float | None, water_ml: float | None) -> bool:
        self._need("nutrition")
        return self._insert("INSERT OR IGNORE INTO nutrition(origin,residency,ext_id,day,kcal,protein_g,carbs_g,fat_g,"
                            "water_ml,imported) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (origin, residency_of(origin), str(ext_id), day, kcal, protein_g, carbs_g, fat_g,
                             water_ml, self._clock())) is not None

    def add_activity(self, origin: str, ext_id: str, started: float, kind: str, distance_m: float | None,
                     duration_s: float | None, avg_hr: float | None, elevation_m: float | None,
                     tz_assumed: bool = False) -> bool:
        self._need("activity")
        return self._insert("INSERT OR IGNORE INTO activities(origin,residency,ext_id,started,tz_assumed,kind,"
                            "distance_m,duration_s,avg_hr,elevation_m,imported) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (origin, residency_of(origin), str(ext_id), started, int(tz_assumed), kind[:40],
                             distance_m, duration_s, avg_hr, elevation_m, self._clock())) is not None

    # ---- the coach's advice log ---------------------------------------------------------------------
    def log_advice(self, topic: str, text: str, facts: str, rules: list[str]) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO advice(ts,topic,text,facts,rules) VALUES(?,?,?,?,?)",
                                   (self._clock(), topic[:40], text[:600], facts[:1200], json.dumps(rules[:6])))
            self._db.execute("DELETE FROM advice WHERE id <= ?", (cur.lastrowid - 500,))  # keep the newest 500
            return cur.lastrowid

    def advice(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM advice ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "rules": json.loads(r["rules"])} for r in rows]

    def last_advice(self) -> dict | None:
        rows = self.advice(limit=1)
        return rows[0] if rows else None

    # ---- your own foods and recipes ------------------------------------------------------------------
    def add_food(self, name: str, serving: str, *, kcal: float, protein_g: float, carbs_g: float = 0.0,
                 fat_g: float = 0.0, vegetarian: bool = False, allergens: str = "", cost_minor: int | None = None,
                 source: str = "you") -> None:
        name = " ".join(name.split())[:80]
        if not name:
            raise ValueError("a food needs a name")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO foods(name,serving,kcal,protein_g,carbs_g,fat_g,vegetarian,allergens,cost_minor,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET serving=excluded.serving, "
                "kcal=excluded.kcal, protein_g=excluded.protein_g, carbs_g=excluded.carbs_g, fat_g=excluded.fat_g, "
                "vegetarian=excluded.vegetarian, allergens=excluded.allergens, cost_minor=excluded.cost_minor, "
                "source=excluded.source",
                (name, serving[:40], kcal, protein_g, carbs_g, fat_g, int(vegetarian), allergens[:80].lower(),
                 cost_minor, source))

    def foods(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM foods ORDER BY name")]

    def remove_food(self, name: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM foods WHERE name=?", (name,)).rowcount > 0

    def add_recipe(self, name: str, items: list[tuple[str, float]]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO recipes(name,items) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET "
                             "items=excluded.items", (" ".join(name.split())[:80], json.dumps(items)))

    def recipes(self) -> list[dict]:
        with self._lock:
            return [{"name": r["name"], "items": [tuple(i) for i in json.loads(r["items"])]}
                    for r in self._db.execute("SELECT * FROM recipes ORDER BY name")]

    # ---- goals (objectives and key results) and daily check-ins -------------------------------------
    def add_okr_row(self, objective: str, key_result: str, unit: str, target: float, due: str, source: str) -> int:
        if target <= 0:
            raise ValueError("a key result needs a target above zero")
        datetime.strptime(due, "%Y-%m-%d")
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO okrs(objective,key_result,unit,target,due,source,created) "
                                   "VALUES(?,?,?,?,?,?,?)",
                                   (objective[:80], key_result[:80], unit[:20], target, due, source[:80], self._clock()))
            return cur.lastrowid

    def okrs(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM okrs ORDER BY id")]

    def update_okr(self, okr_id: int, current: float) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE okrs SET current=? WHERE id=?", (float(current), okr_id))

    def remove_okr(self, okr_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM okrs WHERE id=?", (okr_id,))

    def save_checkin_row(self, day: str, energy: str | None, priority_done: bool | None) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO checkins(day) VALUES(?)", (day,))
            if energy is not None:
                self._db.execute("UPDATE checkins SET energy=? WHERE day=?", (energy, day))
            if priority_done is not None:
                self._db.execute("UPDATE checkins SET priority_done=? WHERE day=?", (int(priority_done), day))

    def checkins(self, start_day: str, end_day: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM checkins WHERE day>=? AND day<=? ORDER BY day", (start_day, end_day))]

    # ---- an injury log (descriptive, not clinical) ---------------------------------------------------
    def log_injury(self, area: str, note: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO injuries(area,note,started) VALUES(?,?,?)",
                                   (" ".join(area.lower().split())[:30], " ".join(note.split())[:200], self._clock()))
            return cur.lastrowid

    def injuries(self, active_only: bool = False) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM injuries" + (" WHERE cleared_at IS NULL" if active_only else "") + " ORDER BY id"
            return [dict(r) for r in self._db.execute(sql)]

    def clear_injury(self, injury_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE injuries SET cleared_at=? WHERE id=? AND cleared_at IS NULL",
                             (self._clock(), injury_id))

    # ---- small internal state and after-workout feedback --------------------------------------------
    def meta_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                             "value=excluded.value", (key, str(value)))

    def save_feedback(self, workout_id: int, kind: str, value: str) -> None:
        if kind not in ("effort", "energy", "soreness"):
            raise ValueError("feedback is effort, energy or soreness")
        with self._lock, self._db:
            self._db.execute("INSERT INTO feedback(workout_id,kind,value,ts) VALUES(?,?,?,?) ON CONFLICT"
                             "(workout_id,kind) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                             (workout_id, kind, value[:20], self._clock()))

    def feedback(self, workout_id: int) -> dict[str, str]:
        with self._lock:
            return {r["kind"]: r["value"] for r in
                    self._db.execute("SELECT kind, value FROM feedback WHERE workout_id=?", (workout_id,))}

    # ---- imports ----------------------------------------------------------------------------------
    def seen_file(self, digest: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM imports WHERE digest=?", (digest,)).fetchone() is not None

    def log_import(self, digest: str, origin: str, rows: int, added: int) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO imports(digest,origin,rows,added,at) VALUES(?,?,?,?,?)",
                             (digest, origin, rows, added, self._clock()))

    # ---- reading ----------------------------------------------------------------------------------
    def max_workout_id(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COALESCE(MAX(id), 0) FROM workouts").fetchone()[0])

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {d: self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t, d in TABLE_DOMAIN.items()}

    def rows(self, table: str) -> list[dict]:
        if table not in ("workouts", "sets", "body", "sleep", "nutrition", "activities"):
            raise ValueError("unknown table")
        with self._lock:
            return [dict(r) for r in self._db.execute(f"SELECT * FROM {table} ORDER BY id")]

    def facts_pack(self, days: int = 7) -> str:
        """Aggregates for a model to explain: a few lines, no ids, no titles, no single-row detail."""
        since = self._clock() - days * 86400
        since_day = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d")
        lines: list[str] = []
        with self._lock:
            q = self._db.execute
            if self.consent("training"):
                n = q("SELECT COUNT(*) FROM workouts WHERE started>=? AND residency!='local_only'", (since,)).fetchone()[0]
                if n:
                    vol = q("SELECT SUM(s.weight_kg*s.reps) FROM sets s JOIN workouts w ON w.id=s.workout_id "
                            "WHERE w.started>=? AND w.residency!='local_only'", (since,)).fetchone()[0] or 0
                    top = [r[0] for r in q("SELECT s.exercise FROM sets s JOIN workouts w ON w.id=s.workout_id WHERE "
                                           "w.started>=? AND w.residency!='local_only' GROUP BY s.exercise ORDER BY "
                                           "COUNT(*) DESC LIMIT 3", (since,))]
                    lines.append(f"Training, last {days} days: {n} workouts, about {round(vol):d} kg of volume; "
                                 f"most trained: {', '.join(top)}.")
            if self.consent("body"):
                r = q("SELECT value, unit FROM body WHERE kind='weight' AND ts>=? AND residency!='local_only' "
                      "ORDER BY ts DESC LIMIT 1", (since,)).fetchone()
                if r:
                    lines.append(f"Latest weight: {r['value']:g} {r['unit'] or 'kg'}.")
            if self.consent("sleep"):
                r = q("SELECT AVG(minutes), COUNT(*) FROM sleep WHERE start>=? AND residency!='local_only'",
                      (since,)).fetchone()
                if r[1]:
                    lines.append(f"Sleep: {r[0] / 60:.1f} h a night on average over {r[1]} nights.")
            if self.consent("nutrition"):
                r = q("SELECT AVG(kcal), AVG(protein_g), COUNT(*) FROM nutrition WHERE day>=? AND "
                      "residency!='local_only'", (since_day,)).fetchone()
                if r[2]:
                    lines.append(f"Food logged on {r[2]} days: about {round(r[0] or 0):d} kcal and "
                                 f"{round(r[1] or 0):d} g protein a day.")
            if self.consent("activity"):
                r = q("SELECT COUNT(*), SUM(distance_m) FROM activities WHERE started>=? AND residency!='local_only'",
                      (since,)).fetchone()
                if r[0]:
                    lines.append(f"Other activity: {r[0]} sessions, {(r[1] or 0) / 1000:.1f} km.")
        return " ".join(lines) or "No health data in the last %d days." % days


_DOMAIN_WORDS = {
    "training": ("workout", "workouts", "training", "gym", "lifting", "exercise"),
    "nutrition": ("food", "nutrition", "meal", "meals", "calorie", "calories", "diet"),
    "sleep": ("sleep",),
    "body": ("weight", "body", "measurement", "measurements"),
    "activity": ("run", "runs", "activity", "activities", "cardio", "strava"),
}
_STOP = re.compile(r"^(?:stop|pause|halt) (?:collecting|tracking|recording|logging|storing) (?:my )?health"
                   r"(?: data| information| stuff)?$|^(?:revoke|withdraw) (?:my )?health (?:consent|permission)$")
_DELETE = re.compile(r"^(yes,? )?(?:delete|erase|remove|wipe) (?:all )?(?:of )?(?:my )?([a-z ]+?) "
                     r"(?:data|records|history|logs?)$")


class HealthCommands:
    """The spoken phrases that need no model: stop collecting, and delete (which asks for a second, explicit yes)."""

    def __init__(self, hub: "HealthHub"):
        self.hub = hub
        self._pending: str | None = None

    def _domains(self, words: str) -> list[str]:
        words = words.strip()
        if words in ("health", "all", "all my health", "everything"):
            return list(DOMAINS)
        return [d for d, ws in _DOMAIN_WORDS.items() if words in ws or words.split(" ")[0] in ws]

    @staticmethod
    def _phrase(text: str) -> str:
        return " ".join(re.sub(r"[?!.,']", " ", text.lower()).split())

    def stop_phrase(self, text: str) -> str | None:
        """Only the protective phrase (safe from any chat): stop collecting. Deleting is not offered here."""
        if not _STOP.match(self._phrase(text)):
            return None
        self._pending = None
        on = [d for d, v in self.hub.consents().items() if v]
        if not on:
            return "I wasn't collecting any health data."
        held = sum(self.hub.counts()[d] for d in on)
        for d in on:
            self.hub.revoke(d)
        return (f"Stopped collecting health data ({', '.join(on)}). I still hold {held} record"
                f"{'s' * (held != 1)}; say 'delete my health data' on the laptop and I'll ask you to confirm.")

    def handle(self, text: str) -> str | None:
        phrase = self._phrase(text)
        stopped = self.stop_phrase(text)
        if stopped is not None:
            return stopped
        m = _DELETE.match(phrase)
        if m:
            domains = self._domains(m.group(2))
            if not domains:
                self._pending = None
                return None
            key = ",".join(domains)
            if m.group(1) and self._pending == key:
                self._pending = None
                n = sum(self.hub.delete_domain(d) for d in domains)
                return f"Deleted {n} record{'s' * (n != 1)}."
            self._pending = key
            n = sum(self.hub.counts()[d] for d in domains)
            return (f"That removes {n} record{'s' * (n != 1)} for good. Say 'yes, delete my {m.group(2)} data' to "
                    "confirm; anything else cancels.")
        self._pending = None  # any other sentence cancels a waiting delete
        return None


def parse_ts(value: str, assume_utc_offset_minutes: int | None = None) -> tuple[float, bool]:
    """(epoch seconds, timezone was assumed). A string with no offset is read in the laptop's local time."""
    text = str(value).strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if when.tzinfo is None:
        return when.astimezone().timestamp(), True  # naive: the local zone is assumed (and the doctor will say so)
    return when.timestamp(), False
