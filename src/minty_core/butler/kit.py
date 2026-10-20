"""One handle on the butler for the plugin, the voice router, the loops and Telegram: nothing is created on disk
until `butler.enabled` is on and something asks for it."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from minty_core.butler.domain import Butler
from minty_core.butler.checkin import CheckIn, CheckInError
from minty_core.butler.domains import all_domains
from minty_core.butler.store import ButlerStore
from minty_core.butler.urgency import Digest, Interruptions
from minty_core.timefmt import clock_text

OFF = "The butler is switched off (butler.enabled on the Switches page)."


class ButlerOff(RuntimeError):
    pass


class ButlerProvider:
    def __init__(self, config: Callable, directory: Path, clock: Callable[[], float] = time.time, receipts=None):
        self._config, self._dir, self._clock = config, Path(directory), clock
        self._receipts = receipts  # receipts.ReceiptStore, for "what did you do for me today"
        self._butler: Butler | None = None
        self._store: ButlerStore | None = None
        self._key: tuple | None = None
        self._last_review = ""
        self.last_dump: tuple[float, list] | None = None  # (when, candidates) of the latest brain-dump sort

    def active(self) -> bool:
        return bool(self._config().butler.enabled)

    def butler(self) -> Butler:
        cfg = self._config().butler
        if not cfg.enabled:
            raise ButlerOff(OFF)
        key = tuple(cfg.domains)
        if self._butler is None or key != self._key:
            if self._store is None:
                self._store = ButlerStore(self._dir / "butler.db", clock=self._clock)
            known = all_domains()
            self._butler = Butler(self._store, {n: known[n] for n in cfg.domains if n in known}, clock=self._clock)
            self._key = key
        return self._butler

    _LOG = re.compile(r"^(?:what|show|read)(?: me)?(?: did you do| have you done| did you change| have you changed)"
                      r"(?: for me)?(?: (?P<when>today|yesterday))?$|^(?:the )?daily log$", re.I)
    _UNDO = re.compile(r"^undo(?: (?:number|#))? ?(?P<n>\d+|that|it)$", re.I)

    def _log_phrases(self, text: str) -> str | None:
        from datetime import datetime, timedelta

        from minty_core.butler.daily_log import render

        low = " ".join(str(text).strip().rstrip("?.!").split())
        checked = self._checkin_phrases(low)
        if checked is not None:
            return checked
        m = self._LOG.match(low)
        if m and self._receipts is not None:
            when = (m.groupdict().get("when") or "today").lower()
            start = datetime.fromtimestamp(self._clock()).replace(hour=0, minute=0, second=0, microsecond=0)
            if when == "yesterday":
                start, end = start - timedelta(days=1), start
            else:
                end = start + timedelta(days=1)
            rows = [r for r in self._receipts.since(start.timestamp()) if r.ts < end.timestamp()]
            undo = self.butler().store.undo_entries(5) if when == "today" else []
            return render(rows, when, undo=undo)
        m = self._UNDO.match(low)
        if m:
            store = self.butler().store
            n = m.group("n").lower()
            if n in ("that", "it"):
                entries = store.undo_entries(1)
                if not entries:
                    return "There is nothing I can undo right now."
                n = str(entries[0]["id"])
            return "Undone." if store.apply_undo(int(n)) else \
                "I can't undo that one (it is already undone, or it is not undoable)."
        return None

    def _digest(self) -> Digest:
        return Digest(self._dir / "butler-digest.json", self._clock)

    def deliver(self, publish: Callable[[str], None], publish_urgent: Callable[[str], None] | None = None) -> int:
        """Say what is due, once each. What is time-sensitive goes out at once (publish_urgent); the rest waits for
        the morning or evening digest. Returns how many messages went out."""
        cfg = self._config().butler
        if not (cfg.enabled and cfg.reminders):
            return 0
        butler = self.butler()
        now = self._clock()
        urgent = publish_urgent or publish
        digest = self._digest()
        ints = Interruptions(digest, cfg.max_interrupts_per_day, self._clock)
        sent = 0
        for notice in butler.notices(now):
            level = ints.route(notice) if cfg.urgency else "now"
            butler.mark(notice, now)
            if level == "now":
                urgent(notice.text)
                sent += 1
            else:
                digest.add(notice.text, level)
        return sent + (self._flush_digests(cfg, digest, publish, now) if cfg.urgency else 0)

    def _flush_digests(self, cfg, digest: Digest, publish, now: float) -> int:
        from datetime import datetime

        moment = datetime.fromtimestamp(now)
        sent = 0
        for slot in cfg.digest_times:
            if slot in digest.slots_done():
                continue
            try:
                hh, mm = (int(x) for x in slot.split(":"))
            except ValueError:
                continue
            slot_at = moment.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if moment < slot_at:
                continue
            digest.mark_slot(slot)
            if (moment - slot_at).total_seconds() > 2 * 3600:
                continue  # that slot passed while I was not running: not worth a late digest
            lines = digest.flush("today")
            if moment.weekday() == 6 and slot == cfg.digest_times[-1]:
                lines += [f"(this week) {t}" for t in digest.flush("later")]
            if lines:
                publish(("Today: " if moment.weekday() != 6 or not any(
                    line.startswith("(this week)") for line in lines) else "") + "; ".join(lines))
                sent += 1
        return sent

    # ---- the check-in timer ---------------------------------------------------------------------------
    def check_in(self) -> CheckIn:
        return CheckIn(self._dir / "checkin.json", self._clock)

    async def check_in_step(self, send_contact, local: Callable[[str], None]) -> int:
        """Run the timer once: ask, or tell the contact. `send_contact(chat_id, text)` is async and returns ok."""
        cfg = self._config().butler
        if not (cfg.enabled and cfg.checkin):
            return 0
        done = 0
        for action in self.check_in().tick(cfg.checkin_user_name, cfg.checkin_grace_min):
            done += 1
            if action.kind == "ask":
                local(action.text)
                continue
            name = cfg.checkin_contact_name or "your contact"
            ok = False
            if cfg.checkin_contact_chat and send_contact is not None:
                try:
                    ok = bool(await send_contact(cfg.checkin_contact_chat, action.text))
                except Exception:  # noqa: BLE001
                    ok = False
            local(f"I told {name} you missed your check-in." if ok else
                  "You missed your check-in and I could not reach your contact. Please let someone know you are okay.")
        return done

    _CHECK_START = re.compile(r"^(?:please )?(?:start (?:a )?)?check[- ]?in(?: timer)?(?: with me)?(?: (?:in|for))? "
                              r"(?P<n>\d+) (?P<unit>minute|hour)s?$", re.I)
    _CHECK_OK = re.compile(r"^(?:i'?m |i am )?(?:fine|safe|ok|okay|back|home|all good|all okay)$", re.I)
    _CHECK_CANCEL = re.compile(r"^cancel (?:the |my )?check[- ]?in$", re.I)

    def _checkin_phrases(self, low: str) -> str | None:
        from datetime import datetime

        cfg = self._config().butler
        if not cfg.checkin:
            return None
        ci = self.check_in()
        m = self._CHECK_START.match(low)
        if m:
            n, unit = int(m.group("n")), m.group("unit").lower()
            try:
                state = ci.start(n * (60 if unit == "hour" else 1))
            except CheckInError as e:
                return str(e)
            at = clock_text(state["due"])
            who = cfg.checkin_contact_name or "your contact"
            return (f"Check-in set for {n} {unit}{'s' * (n != 1)}: I'll ask you at {at}. If I don't hear back "
                    f"I'll tell {who}.")
        if self._CHECK_OK.match(low) and ci.state() is not None:
            ci.confirm()
            return "Good. The check-in is closed."
        if self._CHECK_CANCEL.match(low):
            return "Check-in cancelled." if ci.cancel() else "There is no check-in open."
        return None

    def weekly_review(self, standing, publish: Callable[[str], None]) -> bool:
        """On Sundays (once), the standing approvals and how they were used. Returns True when it was said."""
        import datetime as _dt

        now = self._clock()
        day = _dt.datetime.fromtimestamp(now)
        if day.weekday() != 6 or day.hour < 18 or not self._config().butler.standing_approvals:
            return False
        stamp = day.strftime("%Y-%m-%d")
        if self._last_review == stamp or not standing.active():
            return False
        self._last_review = stamp
        publish(standing.review())
        return True

    def capture(self, text: str) -> str | None:
        """For the voice router: a reply when it is butler business, else None. Never raises."""
        if not self.active():
            return None
        try:
            reply = self._log_phrases(text)
            return reply if reply is not None else self.butler().capture(text)
        except Exception:  # noqa: BLE001 — a butler problem must not stop a normal request
            return None
