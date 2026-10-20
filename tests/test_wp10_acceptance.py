"""WP-10 acceptance, through the app wiring with the fake Bot API: the three checks the plan names."""
import asyncio
import json
import logging

import numpy as np

from tests.telegram_fake import FakeTelegram
from tests.test_telegram_runtime import OWNER, make_ctx, until
from minty_core.audio.types import Audio
from minty_core.telegram.media import ogg_to_pcm, pcm_to_ogg
from minty_core.telegram.runtime import build_runtime

SECRET = "gsk_" + "a1B2c3D4e5F6g7H8i9J0" * 2
PEER_ID = 700100200


class Spy:
    history: list = []

    def __init__(self, answer="It is half past noon."):
        self.asked, self.answer = [], answer

    async def respond(self, text, **kw):
        self.asked.append((text, kw))
        yield self.answer


class STT:
    async def transcribe(self, pcm, rate=16000):
        return "what time is it"


class TTS:
    async def synthesize(self, text):
        t = np.arange(22050) / 22050
        return Audio((np.sin(2 * np.pi * 200 * t) * 7000).astype(np.int16).tobytes(), 22050)


def ogg_note(seconds=1.0):
    t = np.arange(int(16000 * seconds)) / 16000
    return pcm_to_ogg((np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16).tobytes(), 16000)


def with_voice(ctx):
    from types import SimpleNamespace

    ctx.pipeline = SimpleNamespace(stt=STT(), tts=TTS())
    ctx.agent = Spy()
    return ctx


async def _stop(runtime, task):
    runtime.stop()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_voice_note_round_trips_as_text_in_and_a_playable_voice_out(tmp_path):
    ctx, fake = with_voice(make_ctx(tmp_path)), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    task = asyncio.create_task(runtime.run())
    try:
        data = ogg_note()
        fake.add_file("v1", data, "voice/v1.oga")
        fake.say(OWNER, "", voice={"file_id": "v1", "duration": 1, "file_size": len(data)})
        assert await until(lambda: fake.media)
        assert ctx.agent.asked[0][0] == "what time is it"  # the transcript, acted on as text
        voice = fake.media[0]
        assert voice["method"] == "sendVoice" and voice["data"][:4] == b"OggS"
        assert len(ogg_to_pcm(voice["data"])) > 16000  # it decodes: a playable voice file about a second long
        assert any("half past noon" in t for t in fake.texts(OWNER))  # and the words are in the chat too
    finally:
        await _stop(runtime, task)


async def test_a_photo_with_a_planted_secret_never_reaches_a_model_or_a_log(tmp_path, monkeypatch, caplog):
    from minty_core.telegram import intake

    async def fake_ocr(self, path):
        return f"Router login\nusername admin\napi key {SECRET}"

    monkeypatch.setattr(intake.TesseractOCR, "__call__", fake_ocr)
    monkeypatch.setattr(intake.TesseractOCR, "available", lambda self: True)
    ctx, fake = with_voice(make_ctx(tmp_path)), FakeTelegram()
    runtime = build_runtime(ctx, client=fake.client, offer_every_s=0.05)
    caplog.set_level(logging.DEBUG)
    task = asyncio.create_task(runtime.run())
    try:
        fake.add_file("p1", b"\xff\xd8\xff-jpeg", "photos/p1.jpg")
        fake.say(OWNER, "", caption="what is this?", photo=[{"file_id": "p1", "file_size": 9}])
        assert await until(lambda: fake.texts(OWNER))
        assert ctx.agent.asked == []  # no model, local or online, was given the text
        said = "\n".join(fake.texts(OWNER))
        assert "kept it on this laptop" in said and SECRET not in said and SECRET not in caplog.text
        assert any((tmp_path / "telegram-inbox").iterdir())  # the picture itself is on the laptop, owner-only
    finally:
        await _stop(runtime, task)


def machine(tmp_path, name, relay=True, peers=None):
    ctx = with_voice(make_ctx(tmp_path))
    data = ctx.store.config.model_dump(mode="json")
    data["telegram"].update(machine_name=name, relay=relay, relay_peers=peers or {})
    ctx.store.update(data)
    ws = tmp_path / "ws"
    ws.mkdir()
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {"browser": {"folder": str(ws)}}
    ctx.store.update(data)
    return ctx, ws


def deliver(src_fake, dst_fake, dst_gateway_peer_id):
    """What Telegram would do: the document one bot sent shows up as an update at the other bot."""
    media = src_fake.media[-1]
    dst_fake.add_file("rf", media["data"], "documents/rf.bin")
    return dst_fake.say(dst_gateway_peer_id, "", caption=media["caption"],
                        document={"file_id": "rf", "file_name": media["filename"], "file_size": len(media["data"])},
                        **{"from": {"id": dst_gateway_peer_id, "is_bot": True}})


async def test_relay_is_off_until_both_ends_are_on_and_a_loop_dies_at_hop_two(tmp_path):
    laptop, ws = machine(tmp_path / "l", "laptop", peers={"pc": PEER_ID})
    pc, _ = machine(tmp_path / "p", "pc", relay=False, peers={"laptop": PEER_ID + 1})
    fl, fp = FakeTelegram(), FakeTelegram()
    rl, rp = build_runtime(laptop, client=fl.client), build_runtime(pc, client=fp.client)
    (ws / "notes.txt").write_text("hello from the laptop")
    # the laptop sends; the PC has relay off, so it drops it without a sound
    assert "Sent" in await rl.gateway.handlers["/relay"](OWNER, "pc notes.txt")
    await rp.gateway.handle_update(deliver(fl, fp, PEER_ID + 1))
    assert fp.sent == [] and "getFile" not in fp.calls
    # switch the PC on: now it takes the file, tells its owner, and acknowledges once
    data = pc.store.config.model_dump(mode="json")
    data["telegram"]["relay"] = True
    pc.store.update(data)
    await rp.gateway.handle_update(deliver(fl, fp, PEER_ID + 1))
    assert any("notes.txt" in t for t in fp.texts(OWNER))
    acks = [m for m in fp.sent if m["chat_id"] == PEER_ID + 1]
    assert len(acks) == 1 and json.loads(acks[0]["text"].split(" ", 1)[1])["hop"] == 1
    # the ack reaches the laptop: it ends there, nothing is sent back
    ack_update = fl.say(PEER_ID, "", caption=acks[0]["text"], **{"from": {"id": PEER_ID, "is_bot": True}})
    before = len(fl.sent) + len(fl.media)
    await rl.gateway.handle_update(ack_update)
    assert len(fl.sent) + len(fl.media) == before
    # and a message that already made two hops is dropped on arrival
    looped = json.dumps({"from": "pc", "to": "laptop", "id": "loop", "hop": 2, "intent": "file"})
    update = fl.say(PEER_ID, "", caption="MINTY-RELAY/1 " + looped, document={"file_id": "x", "file_size": 3},
                    **{"from": {"id": PEER_ID, "is_bot": True}})
    await rl.gateway.handle_update(update)
    assert len(fl.sent) + len(fl.media) == before
