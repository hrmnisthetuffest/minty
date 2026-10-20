"""Start listening from a button or key instead of saying the name; saying the name still works."""
import asyncio

from fastapi.testclient import TestClient

from minty_core.api import create_app
from minty_core.context import build_context
from tests.test_pipeline import build, finish, wait_until


async def test_listen_now_starts_a_turn_without_the_name(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]])
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    assert p.listen_now() is True
    f.audio.push(b"x", b"SPEECH", b"END")  # the frame being read, then the question
    await wait_until(lambda: f.player.played == [b"It is noon."])
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"]


async def test_the_name_still_works_afterwards(tmp_path):
    p, f = build(tmp_path, stt_texts=["first", "second"], replies=[["One."], ["Two."]])
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    p.listen_now()
    f.audio.push(b"x", b"SPEECH", b"END")
    await wait_until(lambda: b"One." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Two." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["first", "second"]


async def test_nothing_happens_while_muted_and_it_is_not_remembered(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    p.muted = True
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    assert p.listen_now() is False
    f.audio.push(b"x")
    await asyncio.sleep(0.05)
    p.muted = False
    f.audio.push(b"y", b"SPEECH", b"END")  # no name was said and no button pressed: this is just background
    await asyncio.sleep(0.1)
    assert f.agent.heard == [] and f.stt.calls == []
    await finish(p, f, task)


async def test_pressing_listen_while_it_is_speaking_cuts_in(tmp_path):
    p, f = build(tmp_path, stt_texts=["first", "second"], replies=[["One.", "Two."], ["Reply two."]], player_delay=0.4)
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"One." in f.player.played)
    assert p.listen_now() is True
    await wait_until(lambda: f.player.stopped >= 1)  # the sound stopped; now it listens
    f.audio.push(b"SPEECH", b"END")
    await wait_until(lambda: b"Reply two." in f.player.played)
    await finish(p, f, task)
    assert b"Two." not in f.player.played and f.agent.heard == ["first", "second"]


async def test_it_does_nothing_when_it_is_already_listening(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[])
    p._set_state("listening")
    assert p.listen_now() is False


def test_the_panel_api_can_press_listen_and_mute(tmp_path):
    ctx = build_context(tmp_path)
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    assert client.post("/listen", headers=auth).status_code == 503  # no voice pipeline

    class Pipe:
        muted, state = False, "idle"
        pressed = 0

        def listen_now(self):
            self.pressed += 1
            return not self.muted

    ctx.pipeline = Pipe()
    assert client.post("/listen", headers=auth).status_code == 204 and ctx.pipeline.pressed == 1
    ctx.pipeline.muted = True
    assert client.post("/listen", headers=auth).status_code == 409  # nothing to start


async def test_listen_goes_straight_to_listening_with_no_acknowledgement(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]], wake_ack="Yes, sir?")
    states = f.bus.subscribe({"state"})
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.02)
    assert p.listen_now() is True
    assert p.state == "listening"  # at once, before the next audio frame even arrives
    f.audio.push(b"x", b"SPEECH", b"END")
    await wait_until(lambda: b"It is noon." in f.player.played)
    await finish(p, f, task)
    seen = [e["state"] for e in list(states._queue)]
    assert seen[0] == "listening"
    assert [x for x in seen if x != "listening"][0] in ("thinking", "processing")  # nothing was said before the question was answered
    assert b"Yes, sir?" not in f.player.played  # the name still gets its acknowledgement; the button does not


async def test_the_name_still_gets_its_acknowledgement(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]], wake_ack="Yes, sir?")
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE")
    await wait_until(lambda: b"Yes, sir?" in f.player.played)
    f.audio.push(*[b"x"] * 10, b"SPEECH", b"END")
    await wait_until(lambda: b"Hi." in f.player.played)
    await finish(p, f, task)
