"""Quiet-hours-aware pushes, briefs and silent job pings (W3-06)."""
from datetime import datetime

from tests.test_telegram_gateway import OWNER, build
from minty_core.events import EventBus
from minty_core.telegram.push import PushRouter


def setup(tmp_path, quiet):
    gw, fake, *_ = build(tmp_path)
    clock = {"now": datetime(2026, 10, 15, 22, 0), "quiet": quiet}
    router = PushRouter(gw, EventBus(), quiet=lambda now: clock["quiet"], state_path=tmp_path / "held.json",
                        clock=lambda: clock["now"])
    return gw, fake, router, clock


async def test_a_notice_goes_out_at_once_when_it_is_not_quiet(tmp_path):
    gw, fake, router, _ = setup(tmp_path, quiet=False)
    assert await router.push("Your timer is done.") is True
    assert fake.texts(OWNER) == ["[laptop] Your timer is done."]
    assert fake.sent[0].get("disable_notification") is None


async def test_quiet_hours_hold_it_and_deliver_it_once_as_a_digest_afterwards(tmp_path):
    gw, fake, router, clock = setup(tmp_path, quiet=True)
    assert await router.push("Good morning. Sunny today.") is False
    await router.push("Job 3 (research) finished.", silent=True)
    await router.push("Good morning. Sunny today.")  # the same line again: not held twice
    assert fake.sent == [] and await router.release() == 0  # still quiet
    clock["quiet"] = False
    assert await router.release() == 2
    assert fake.texts(OWNER) == ["[laptop] While you were away, 2 things:\n1. Good morning. Sunny today.\n"
                                 "2. Job 3 (research) finished."]
    assert fake.sent[0]["disable_notification"] is True
    assert await router.release() == 0  # delivered once


async def test_a_single_held_item_is_sent_as_itself(tmp_path):
    gw, fake, router, clock = setup(tmp_path, quiet=True)
    await router.push("Mom's birthday is tomorrow.")
    clock["quiet"] = False
    await router.release()
    assert fake.texts(OWNER) == ["[laptop] Mom's birthday is tomorrow."]


async def test_held_items_survive_a_restart(tmp_path):
    gw, fake, router, clock = setup(tmp_path, quiet=True)
    await router.push("Remember the spikes.")
    clock2 = {"now": clock["now"], "quiet": False}
    again = PushRouter(gw, EventBus(), quiet=lambda now: clock2["quiet"], state_path=tmp_path / "held.json")
    assert await again.release() == 1 and fake.texts(OWNER) == ["[laptop] Remember the spikes."]


def test_job_events_become_silent_pings_once_and_private_jobs_hide_their_details(tmp_path):
    gw, fake, router, _ = setup(tmp_path, quiet=False)
    done = {"type": "job", "id": 3, "goal": "compare two desk lamps", "status": "done", "progress": "Lamp B is cheaper."}
    item = router.from_event(done)
    assert item == {"text": "Job 3 (compare two desk lamps) finished. Lamp B is cheaper.", "silent": True}
    assert router.from_event(done) is None  # the same status again
    assert router.from_event({"type": "job", "id": 3, "status": "running"}) is None
    private = router.from_event({"type": "job", "id": 4, "goal": "my health", "status": "done", "private": True})
    assert "details are on the laptop" in private["text"] and "health" not in private["text"]
    assert router.from_event({"type": "notify", "text": "Hi."}) == {"text": "Hi.", "silent": False}


async def test_nothing_is_pushed_when_nobody_is_paired_or_after_lockdown(tmp_path):
    gw, fake, router, _ = setup(tmp_path, quiet=False)
    gw.suspended = True
    assert await router.push("anything") is False and fake.sent == []


async def test_a_phone_event_from_the_notifier_goes_out_even_in_quiet_hours_and_a_local_notice_never_does(tmp_path):
    gw, fake, router, _ = setup(tmp_path, quiet=True)
    item = router.from_event({"type": "phone", "text": "Mail from Alex: invoice due Friday", "urgent": True})
    assert item == {"text": "Mail from Alex: invoice due Friday", "silent": False, "force": True}  # the notifier already weighed the quiet hours
    await router.push(item["text"], item["silent"], force=item["force"])
    assert fake.texts(OWNER) == ["[laptop] Mail from Alex: invoice due Friday"]
    assert router.from_event({"type": "notify", "text": "Alex: the details", "local": True}) is None  # said aloud here, not sent to a phone
    assert router.from_event({"type": "notify", "text": "Your timer is done."}) == {"text": "Your timer is done.", "silent": False}
