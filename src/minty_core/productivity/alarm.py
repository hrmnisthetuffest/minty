"""Alarms (673): at its time, on its days, once a day; snooze; a gentle ramp and a short brief when it rings.
Plain scheduling: what plays and says is up to the caller. A missed alarm is not rung hours late."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from minty_core.productivity.db import LifeDB

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GRACE = timedelta(minutes=30)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _days(spec: str) -> str:
    s = str(spec or "").strip().lower().replace(" ", "")
    if s in ("", "daily", "everyday"):
        return ""
    if s == "weekdays":
        return "mon,tue,wed,thu,fri"
    if s == "weekends":
        return "sat,sun"
    parts = [p[:3] for p in s.split(",") if p]
    if not parts or any(p not in DAYS for p in parts):
        raise ValueError("Say which days: weekdays, weekends, or like mon,wed.")
    return ",".join(sorted(set(parts), key=DAYS.index))


def ramp(steps: int = 5) -> list[float]:
    """Volumes for a wake-up chime that starts quiet and ends at full."""
    steps = max(2, steps)
    return [round(0.25 + 0.75 * i / (steps - 1), 2) for i in range(steps)]


def alarm_brief(now: datetime, first_class: str = "", due=(), weather: str = "", occasions=()) -> str:
    parts = [f"Good morning. It's {now:%-I:%M}."]
    if first_class:
        parts.append(f"First class: {first_class}.")
    if due:
        parts.append("Due today: " + "; ".join(due) + ".")
    if occasions:
        parts.append("; ".join(occasions) + ".")
    if weather:
        parts.append(f"{weather}.")
    return " ".join(parts)


class Alarms:
    def __init__(self, db: LifeDB):
        self.db = db
        self._ringing: set[int] = set()

    def add(self, at: str, days: str = "", label: str = "", brief: bool = True, once: bool = False) -> int:
        at = str(at).strip()
        if not _HHMM.match(at):
            raise ValueError("Give the time as 24-hour HH:MM, like 06:30.")
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO alarms(at, days, label, brief, once) VALUES (?,?,?,?,?)",
                                     (at, _days(days), " ".join(label.split())[:40], int(brief), int(once)))
            self.db.db.commit()
            return cur.lastrowid

    def remove(self, ident: int) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM alarms WHERE id=?", (ident,)).rowcount
            self.db.db.commit()
        self._ringing.discard(ident)
        return bool(n)

    def list(self) -> list[dict]:
        with self.db.lock:
            return [{"id": r["id"], "at": r["at"], "days": r["days"], "label": r["label"], "brief": bool(r["brief"]),
                     "enabled": bool(r["enabled"]), "once": bool(r["once"])}
                    for r in self.db.db.execute("SELECT * FROM alarms ORDER BY at, id").fetchall()]

    def due(self, now: datetime) -> list[dict]:
        out = []
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM alarms WHERE enabled=1").fetchall()]
            for r in rows:
                today = now.date().isoformat()
                if r["snoozed_until"] is not None:
                    if r["snoozed_until"] <= now.timestamp():
                        self.db.db.execute("UPDATE alarms SET snoozed_until=NULL WHERE id=?", (r["id"],))
                        out.append(r)
                    continue
                if r["last_day"] == today:
                    continue
                if r["days"] and DAYS[now.weekday()] not in r["days"].split(","):
                    continue
                h, m = (int(x) for x in r["at"].split(":"))
                at = datetime.combine(now.date(), time(h, m))
                if at <= now <= at + GRACE:
                    self.db.db.execute("UPDATE alarms SET last_day=?, enabled=? WHERE id=?",
                                       (today, 0 if r["once"] else 1, r["id"]))
                    out.append(r)
            self.db.db.commit()
        return [{"id": r["id"], "at": r["at"], "label": r["label"], "brief": bool(r["brief"])} for r in out]

    def start_ring(self, ident: int) -> None:
        self._ringing.add(ident)

    @property
    def ringing(self) -> list[int]:
        return sorted(self._ringing)

    def snooze(self, now: datetime, minutes: int = 8) -> list[int]:
        ids = sorted(self._ringing)
        until = (now + timedelta(minutes=minutes)).timestamp()
        with self.db.lock:
            for i in ids:
                self.db.db.execute("UPDATE alarms SET snoozed_until=? WHERE id=?", (until, i))
            self.db.db.commit()
        self._ringing.clear()
        return ids

    def stop(self) -> list[int]:
        ids = sorted(self._ringing)
        with self.db.lock:
            for i in ids:
                self.db.db.execute("UPDATE alarms SET snoozed_until=NULL WHERE id=?", (i,))
            self.db.db.commit()
        self._ringing.clear()
        return ids
