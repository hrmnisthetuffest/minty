"""The selfcheck tools through the real plugin host, and the loop."""
import json
import shutil
from datetime import date, datetime, timedelta

import pytest

from minty_core.prompts import PromptLayers
from minty_core.receipts import ReceiptStore
from tests.plugin_helpers import make_host
from tests.test_selfcheck_report import STALE, replayed_week

TODAY = date(2026, 10, 18)  # a Sunday


def at(h, m=0, day=TODAY):
    return datetime(day.year, day.month, day.day, h, m)


class Rig:
    def __init__(self, tmp_path, when=None, **cfg):
        self.events = []
        self.layers = PromptLayers(tmp_path / "cfg")
        self.host = make_host(tmp_path / "cfg", services={"prompts": self.layers,
                                                          "publish": lambda kind, **f: self.events.append((kind, f))})
        data = self.host._store.config.model_dump(mode="json")
        data["selfcheck"].update({"enabled": True, **cfg})
        self.host._store.update(data)
        self.p = self.host._services["selfcheck"]
        self.when = when or at(12)
        self.p._clock = lambda: self.when.timestamp()
        self.tmp = tmp_path

    def write_traces(self):
        folder = self.tmp / "cfg" / "traces"
        folder.mkdir(parents=True, exist_ok=True)
        for day, events in replayed_week().items():
            (folder / f"{day}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

    async def call(self, tool, args):
        return await self.host.call(f"selfcheck_{tool}", args)


async def test_off_until_switched_on(tmp_path):
    r = Rig(tmp_path, enabled=False)
    assert "selfcheck.enabled" in await r.call("report", {})
    assert "selfcheck.enabled" in await r.call("timeline", {})


async def test_the_report_names_the_known_problems_and_makes_proposals_you_decide_on(tmp_path):
    r = Rig(tmp_path)
    assert "No traces for the last 7 days" in await r.call("report", {})
    r.write_traces()
    out = await r.call("report", {})
    assert out.startswith("[untrusted trace text") and "browser_click failed 5 times" in out and "proposals waiting" in out
    listing = await r.call("report", {"action": "proposals"})
    assert "[1]" in listing and "browser_click" in listing and "standing instruction" in listing
    lesson_id = next(int(line[1:line.index("]")]) for line in listing.splitlines() if "adds a standing instruction" in line)
    assert r.host.get_tool("selfcheck_report").risk({"action": "approve"}) == 1
    out = await r.call("report", {"action": "approve", "id": lesson_id})
    assert "standing instructions" in out
    assert "If browser_click fails, say in one sentence what went wrong" in r.layers.instructions()
    assert "unknown element" not in r.layers.instructions()  # the log's words never reach the instruction
    assert "already approved" in await r.call("report", {"action": "approve", "id": lesson_id})
    other = next(int(line[1:line.index("]")]) for line in (await r.call("report", {"action": "proposals"})).splitlines())
    assert "Dropped" in await r.call("report", {"action": "reject", "id": other})
    assert "Say report" in await r.call("report", {"action": "format_disk"})


async def test_the_tip_is_the_same_all_day(tmp_path):
    r = Rig(tmp_path)
    a = await r.call("report", {"action": "tip"})
    assert a and a == await r.call("report", {"action": "tip"})
    r.when = at(12, day=TODAY + timedelta(days=1))
    assert await r.call("report", {"action": "tip"}) != a


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="no rsvg-convert")
async def test_the_card_is_made_and_only_sent_when_you_ask_and_it_is_switched_on(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("report", {"action": "card"})
    png = tmp_path / "cfg" / "cards" / "card-20261018.png"
    assert png.is_file() and str(png) in out and r.events == []
    await r.call("report", {"action": "card", "send": True})
    assert r.events == []  # card_push is off: asking is not enough
    on = Rig(tmp_path / "on", card_push=True)
    out = await on.call("report", {"action": "card", "send": True})
    assert "Sent to your phone" in out and on.events[0][0] == "card" and on.events[0][1]["path"].endswith("card-20261018.png")


async def test_the_timeline_tool_reads_jobs_and_receipts(tmp_path):
    r = Rig(tmp_path)
    assert "Nothing has happened yet" in await r.call("timeline", {})
    store = ReceiptStore(tmp_path / "r.db", clock=lambda: at(9, 30).timestamp())
    store.record("files_write", 1, "Saved notes.txt")
    r.host._services["receipts"] = store
    out = await r.call("timeline", {})
    assert out.startswith("[untrusted") and "Sun 09:30 action: Saved notes.txt (ok)" in out


async def test_the_loop_says_the_tip_the_sunday_report_and_the_card_once_each_inside_their_windows(tmp_path):
    r = Rig(tmp_path, tip_push=True, weekly_report=True, card_push=True)
    r.write_traces()
    r.when = at(8, 40)
    first = await r.p.tick()
    assert len(first) == 1 and first[0].startswith("Tip: ")
    assert await r.p.tick() == []  # said once
    r.when = at(18, 30)
    report = await r.p.tick()
    assert len(report) == 1 and "browser_click failed 5 times" in report[0]
    assert await r.p.tick() == []
    r.when = at(21, 10)
    await r.p.tick()
    cards = [e for e in r.events if e[0] == "card"]
    assert len(cards) == (1 if shutil.which("rsvg-convert") or shutil.which("google-chrome-stable") else 0)
    r.when = at(21, 20)
    await r.p.tick()
    assert len([e for e in r.events if e[0] == "card"]) == len(cards)
    r.when = at(15, day=TODAY + timedelta(days=1))  # Monday afternoon: nothing is due
    assert await r.p.tick() == []


async def test_a_slept_through_time_is_not_said_late_and_everything_off_stays_quiet(tmp_path):
    r = Rig(tmp_path, tip_push=True, weekly_report=True)
    r.when = at(14)
    assert await r.p.tick() == []  # the tip's half-hour window has long passed
    off = Rig(tmp_path / "off", enabled=False, tip_push=True)
    off.when = at(8, 40)
    assert await off.p.tick() == []
