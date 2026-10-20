import asyncio
from minty_core.events import EventBus


async def test_publish_reaches_subscribers_and_unsubscribe_stops():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("state", state="listening")
    assert await q.get() == {"type": "state", "state": "listening"}
    bus.unsubscribe(q)
    bus.publish("state", state="idle")
    assert q.empty()


async def test_full_queue_drops_instead_of_raising():
    bus = EventBus()
    q = bus.subscribe()
    for i in range(500):
        bus.publish("tick", n=i)
    assert q.qsize() <= 100


async def test_publish_from_worker_thread_wakes_the_loop():
    import threading

    bus = EventBus()
    q = bus.subscribe()
    bus.attach_loop(asyncio.get_running_loop())
    threading.Thread(target=lambda: bus.publish("notify", text="hi")).start()
    assert await asyncio.wait_for(q.get(), 1) == {"type": "notify", "text": "hi"}


async def test_filtered_subscription_only_gets_its_types():
    from minty_core.events import EventBus

    bus = EventBus()
    notices, everything = bus.subscribe({"notify"}), bus.subscribe()
    for _ in range(150):
        bus.publish("level", db=-40.0)
    bus.publish("notify", text="timer done")
    assert notices.qsize() == 1 and notices.get_nowait()["text"] == "timer done"
    assert everything.qsize() == 100  # unfiltered queues still cap out
