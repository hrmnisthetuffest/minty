"""Push-to-talk (506): opt-in, no wake detector while on, nothing kept between presses."""
import asyncio
from types import SimpleNamespace

from minty_core.config import ConfigStore
from minty_core.voice.ptt import PushToTalk
from tests.test_pipeline import build, finish, wait_until


def test_it_is_off_by_default(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.load().audio.push_to_talk is False


async def test_presses_only_count_while_it_is_enabled():
    on = {"v": False}
    ptt = PushToTalk(lambda: on["v"])
    ptt.press()
    assert ptt.held is False
    on["v"] = True
    ptt.press()
    assert ptt.held is True
    ptt.release()
    assert ptt.held is False
    waiter = asyncio.create_task(ptt.wait_press())
    await asyncio.sleep(0)
    assert not waiter.done()
    ptt.press()
    await asyncio.wait_for(waiter, 1)


def _build(tmp_path, texts, ptt_on=True):
    p, f = build(tmp_path, stt_texts=texts, replies=[["Done."], ["Again."]], follow_up=True)
    data = p.store.config.model_dump(mode="json")
    data["audio"]["push_to_talk"] = ptt_on
    data["audio"]["follow_up"] = True
    data["audio"]["barge_in"] = False
    p.store.update(data)
    ptt = PushToTalk(lambda: p.store.config.audio.push_to_talk)
    p.ptt = ptt
    return p, f, ptt


async def test_the_wake_word_does_nothing_while_it_is_on_and_a_press_starts_a_turn(tmp_path):
    p, f, ptt = _build(tmp_path, ["what time is it"])
    f.audio.push(b"WAKE", b"x", b"x")  # the name is ignored while push-to-talk is on
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.1)
    assert f.agent.heard == [] and f.stt.calls == []
    ptt.press()
    f.audio.push(b"SPEECH", b"SPEECH", b"SPEECH")
    await asyncio.sleep(0.1)
    ptt.release()
    f.audio.push(b"tail", b"tail", b"tail")
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"]


async def test_no_audio_is_kept_between_presses(tmp_path):
    p, f, ptt = _build(tmp_path, ["hello"])
    f.audio.push(*([b"SPEECH"] * 10))  # talking with the key up
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.15)
    assert f.stt.calls == [] and f.agent.heard == []
    ptt.press()
    f.audio.push(b"SPEECH", b"SPEECH")
    await asyncio.sleep(0.1)
    ptt.release()
    f.audio.push(b"t", b"t", b"t")
    await wait_until(lambda: f.stt.calls)
    await finish(p, f, task)
    assert len(f.stt.calls) == 1 and f.stt.calls[0].count(b"SPEECH") <= 3  # only what was held, not the earlier ten


def test_the_voice_phrases_turn_it_on_and_off_and_the_api_presses_the_key(tmp_path):
    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.commands import CommandRouter
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    router = CommandRouter(ctx.agent, ctx.store)
    assert "Push to talk is on" in router.handle("turn on push to talk")
    assert ctx.store.config.audio.push_to_talk is True
    assert "Push to talk is off" in router.handle("turn off push to talk")
    assert ctx.store.config.audio.push_to_talk is False
    ctx.services["ptt"] = PushToTalk(lambda: True)
    client = TestClient(create_app(ctx))
    headers = {"Authorization": f"Bearer {ctx.token}"}
    assert client.post("/ptt/press", headers=headers).status_code == 204
    assert ctx.services["ptt"].held is True
    assert client.post("/ptt/release", headers=headers).status_code == 204
    assert ctx.services["ptt"].held is False
    assert client.post("/ptt/explode", headers=headers).status_code == 404
    assert client.post("/ptt/press").status_code in (401, 403)
