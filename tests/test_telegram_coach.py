"""Debriefs and briefs on the phone: buttons for one-tap feedback, quiet hours, private days (LF-10)."""
import asyncio
from datetime import datetime

from tests.test_telegram_gateway import OWNER, STRANGER, build
from minty_core.events import EventBus
from minty_core.health import HealthHub
from minty_core.telegram.coach import CoachDelivery
from minty_core.telegram.push import PushRouter

DAY = 86400.0


def setup(tmp_path, briefs=True, quiet=False, private=False):
    gw, fake, store, _ = build(tmp_path, private=private)
    data = store.config.model_dump(mode="json")
    data["coach"].update(enabled=True, briefs=briefs)
    store.update(data)
    hub = HealthHub(tmp_path / "health.db", clock=lambda: 1_800_000_000.0)
    hub.grant("training")
    hub.add_workout("hevy", "w1", 1_799_990_000.0, "Legs", 3600, [])
    wid = hub.rows("workouts")[0]["id"]
    bus = EventBus()
    push = PushRouter(gw, bus, quiet=lambda now: quiet, state_path=tmp_path / "held.json",
                      clock=lambda: datetime(2026, 10, 15, 12, 0))
    delivery = CoachDelivery(gw, push, lambda: hub)
    delivery.install()
    return gw, fake, hub, bus, push, delivery, wid


def debrief(wid, text="Good session: Legs. PR: Squat 128.3 kg."):
    return {"type": "coach", "kind": "debrief", "text": text, "workout_id": wid}


async def test_a_debrief_arrives_with_one_tap_feedback_buttons(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path)
    assert await delivery.handle(debrief(wid)) == "sent"
    sent = fake.sent[0]
    assert sent["text"].startswith("[laptop] Good session") and sent["chat_id"] == OWNER
    rows = sent["reply_markup"]["inline_keyboard"]
    assert [len(r) for r in rows] == [3, 3, 3]
    assert all(len(b["callback_data"].encode()) <= 64 for r in rows for b in r)
    hard = rows[0][2]
    await gw.handle_update(fake.press(OWNER, hard["callback_data"]))
    assert hub.feedback(wid) == {"effort": "hard"} and fake.answers[-1]["text"] == "Noted."
    await gw.handle_update(fake.press(OWNER, rows[2][1]["callback_data"]))
    assert hub.feedback(wid) == {"effort": "hard", "soreness": "some"}


async def test_a_stranger_cannot_write_feedback_and_a_bad_button_is_harmless(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path)
    await gw.handle_update(fake.press(STRANGER, f"cf:{wid}:e:hard"))
    await gw.handle_update(fake.press(OWNER, "cf:9999:e:hard"))  # no such workout
    await gw.handle_update(fake.press(OWNER, f"cf:{wid}:z:hard"))  # no such question
    assert hub.feedback(wid) == {}


async def test_nothing_is_sent_with_briefs_off_or_on_a_private_day(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path, briefs=False)
    assert await delivery.handle(debrief(wid)) == "off" and fake.sent == []
    gw2, fake2, _, _, _, delivery2, wid2 = setup(tmp_path / "p", private=True)
    assert await delivery2.handle(debrief(wid2)) == "private" and fake2.sent == []


async def test_in_quiet_hours_the_text_is_held_without_buttons(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path, quiet=True)
    assert await delivery.handle(debrief(wid)) == "held"
    assert fake.sent == [] and len(push._held) == 1


async def test_a_pre_workout_brief_is_a_quiet_push(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path)
    assert await delivery.handle({"type": "coach", "kind": "brief", "text": "Next up looks like Push."}) == "sent"
    assert fake.sent[0].get("reply_markup") is None and fake.sent[0].get("disable_notification") is True


async def test_the_bus_loop_delivers_coach_events(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path)
    task = asyncio.create_task(delivery.run(bus))
    await asyncio.sleep(0)
    bus.publish("coach", kind="debrief", text="Good session.", workout_id=wid)
    for _ in range(100):
        if fake.sent:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(fake.sent) == 1


async def test_pause_resume_and_why_work_from_the_chat(tmp_path):
    gw, fake, hub, bus, push, delivery, wid = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "pause coaching"))
    assert hub.profile()["coach_paused"] == "1" and "paused" in fake.texts(OWNER)[-1].lower()
    await gw.handle_update(fake.say(OWNER, "resume coaching"))
    assert hub.profile()["coach_paused"] == "0"
    await gw.handle_update(fake.say(OWNER, "why did you say that"))
    assert "haven't given you any" in fake.texts(OWNER)[-1]
