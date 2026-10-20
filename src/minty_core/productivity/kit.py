"""One handle on the productivity tools for the plugin, the voice phrases and the loops. Nothing is created on disk
until `productivity.enabled` is on and something asks for it."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.productivity.alarm import Alarms, alarm_brief
from minty_core.productivity.daily import DayData, daily_plan, end_of_day, wind_down
from minty_core.productivity.db import LifeDB
from minty_core.productivity.habits import Habits
from minty_core.productivity.meals import Meals
from minty_core.productivity.people import People
from minty_core.productivity.recorder import Recorder
from minty_core.productivity.trips import Trips

OFF = "The productivity tools are switched off (productivity.enabled on the Switches page)."
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class ProductivityOff(RuntimeError):
    pass


class Life:
    def __init__(self, directory: Path, clock: Callable[[], float]):
        self.db = LifeDB(Path(directory) / "life.db")
        self.habits, self.people, self.trips = Habits(self.db), People(self.db, clock), Trips(self.db)
        self.meals, self.alarms = Meals(self.db), Alarms(self.db)


class ProductivityProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time):
        self._config, self._dir, self._services, self._clock = config, Path(directory), services, clock
        self._life: Life | None = None
        self._recorder: Recorder | None = None

    def active(self) -> bool:
        return bool(self._config().productivity.enabled)

    def life(self) -> Life:
        if not self._config().productivity.enabled:
            raise ProductivityOff(OFF)
        if self._life is None:
            self._life = Life(self._dir, self._clock)
            # a person's derived items go with them: butler follow-ups that name them
            self._life.people.on_delete.append(self._forget_in_butler)
        return self._life

    def _forget_in_butler(self, name: str) -> None:
        provider = self._services.get("butler")
        if provider is None or not provider.active():
            return
        butler = provider.butler()
        first = name.split()[0].lower()
        for item in list(butler.store.list("waiting", "open", 500)) + list(butler.store.list("waiting", "done", 500)):
            if first in item.name.lower() or first in str(item.data.get("who", "")).lower():
                butler.store.delete(item.id)

    def recorder(self) -> Recorder:
        cfg = self._config().recorder
        if self._recorder is None:
            folder = Path(cfg.folder).expanduser() if cfg.folder else self._dir / "recordings"
            self._recorder = Recorder(folder, self._clock, max_minutes=cfg.max_minutes, keep_audio=cfg.keep_audio)
        self._recorder.keep_audio, self._recorder.max_minutes = cfg.keep_audio, cfg.max_minutes
        return self._recorder

    def start_recording(self, label: str, publish: Callable) -> str:
        rec = self.recorder()
        reminder = rec.start(label)
        publish("recording", active=True, label=rec.session.label)
        return reminder

    def stop_recording(self, publish: Callable) -> str:
        """Stop, then make the notes in the background (needs a running event loop)."""
        rec = self.recorder()
        session = rec.stop()
        publish("recording", active=False, label=session.label)
        transcribe = self._services.get("transcribe")
        if transcribe is None:
            return "Stopped. I can't turn it into text right now (no speech recogniser)."

        def save(title, markdown, tags):
            vault = self._services.get("vault")
            if vault is not None and vault.active():
                return vault.kit().vault.create(title, markdown, folder="sources", origin="file", tags=tags,
                                                source="recording").id
            out = self._dir / "recording-notes"
            out.mkdir(exist_ok=True)
            path = out / f"{session.id}.md"
            path.write_text(markdown, encoding="utf-8")
            return path.name

        async def work():
            try:
                r = await rec.finish(session, transcribe, save)
                publish("notify", text=f"Your recording is ready: {r.title}, {r.words} words, "
                                       f"{len(r.todos)} action item(s).")
            except Exception as e:  # noqa: BLE001 — say it failed rather than lose it silently
                publish("notify", text=f"I could not make the note from the recording: {e}")

        asyncio.get_running_loop().create_task(work())
        return "Stopped. I am making the notes now and will tell you when they are ready."

    # ---- gathering what the day is made of ------------------------------------------------------------
    def day_data(self, today: date) -> DayData:
        cfg = self._config()
        life = self.life()
        d = DayData()
        tt = cfg.study.timetable
        key = DAYS[today.weekday()]
        d.classes = [f"{p['start']} {p.get('subject', 'Class')}" for p in tt.get(key, []) if "start" in p]
        nxt = DAYS[(today + timedelta(days=1)).weekday()]
        tomorrow = tt.get(nxt, [])
        if tomorrow:
            d.first_tomorrow = f"{tomorrow[0].get('subject', 'class')} at {tomorrow[0].get('start', '')}".strip()
        study = self._services.get("study")
        if study is not None and study.active():
            from minty_core.inbox import Inbox
            from minty_core.study.plan import Task, countdown

            inbox = Inbox(self._dir / "inbox.db")
            try:
                tasks = [Task(f"hw{h.id}", h.subject or "Homework", h.task, 30, h.due)
                         for h in inbox.open_homework() if h.due]
            finally:
                inbox.close()
            plan = study.planner().plan(tasks, today, datetime.now())
            d.study_blocks = [f"{b.start:%H:%M}-{b.end:%H:%M} {b.subject}: {b.title}" for b in plan.blocks
                              if b.day == today]
            kit = study.kit()
            d.exams = [e for e in countdown(kit.exams(), today) if re.search(r"today|tomorrow|in \d+ days", e)][:3]
            d.cards_reviewed = kit.cards.reviews_between(
                datetime.combine(today, datetime.min.time()).timestamp(),
                datetime.combine(today + timedelta(days=1), datetime.min.time()).timestamp())
        butler_p = self._services.get("butler")
        if butler_p is not None and butler_p.active():
            b = butler_p.butler()
            end = datetime.combine(today + timedelta(days=1), datetime.min.time()).timestamp()
            midnight = datetime.combine(today, datetime.min.time()).timestamp()
            if "tasks" in b.domains:
                d.tasks_due = [i.name for i in b.list("tasks") if i.due is not None and i.due < end]
                d.tasks_done = len([i for i in b.list("tasks", "done") if i.updated >= midnight])
                if not d.first_tomorrow:
                    nxt_task = [i for i in b.list("tasks") if i.due is not None and end <= i.due < end + 86400]
                    if nxt_task:
                        d.first_tomorrow = nxt_task[0].name
            if "chores" in b.domains:
                d.chores = [i.name for i in b.list("chores") if i.due is not None and i.due < end]
                with b.store._lock:
                    ticked = b.store._db.execute("SELECT COUNT(*) FROM undo WHERE action='butler.restore' AND ts>=?",
                                                 (midnight,)).fetchone()[0]
                d.chores_done = max(0, ticked - d.tasks_done)
        for h in life.habits.status(today):
            if h["done_today"]:
                d.habits_done.append(h["name"])
            else:
                d.habits_open.append(h["name"] + (f" (streak {h['streak']})" if h["streak"] else ""))
        for o in life.people.upcoming(today, 7):
            when = "today" if o["in_days"] == 0 else "tomorrow" if o["in_days"] == 1 else f"in {o['in_days']} days"
            d.occasions.append(f"{o['who']}'s {o['what'].lower()} {when}")
        vault = self._services.get("vault")
        if vault is not None and vault.active():
            try:
                d.notes_saved = len([n for n in vault.kit().vault.list(None, limit=500)
                                     if n.created[:10] == today.isoformat() and n.folder != "daily"])
            except Exception:  # noqa: BLE001
                pass
        receipts = self._services.get("receipts")
        if receipts is not None:
            midnight = datetime.combine(today, datetime.min.time()).timestamp()
            d.changes = len([r for r in receipts.since(midnight) if r.outcome == "ok"])
        return d

    def plan_text(self, today: date) -> str:
        return daily_plan(today, self.day_data(today))

    def wind_down_text(self, today: date) -> str:
        cfg = self._config()
        from minty_core.study.plan import tomorrow_bag

        bag = tomorrow_bag(cfg.study.timetable, today) if cfg.study.timetable else ""
        alarms = [a for a in self.life().alarms.list() if a["enabled"]]
        alarm = f"Your alarm is at {alarms[0]['at']}." if alarms else ""
        return wind_down(cfg.productivity.wind_down_steps, bag, alarm)

    def summary_text(self, today: date) -> str:
        return end_of_day(today, self.day_data(today))

    def brief_for(self, now: datetime) -> str:
        d = self.day_data(now.date())
        first = d.classes[0] if d.classes else ""
        return alarm_brief(now, first, d.tasks_due[:3], "", d.occasions[:2])
