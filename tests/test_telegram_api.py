"""The Bot API client (W3-01)."""
import httpx
import pytest

from tests.telegram_fake import TOKEN, FakeTelegram
from minty_core.telegram.api import BotAPI, TelegramError


def api(fake, sleeps=None):
    async def sleep(s):
        (sleeps if sleeps is not None else []).append(s)
    return BotAPI(TOKEN, fake.client, sleep=sleep)


async def test_a_round_trip_and_the_methods_the_gateway_needs():
    fake = FakeTelegram()
    bot = api(fake)
    assert (await bot.get_me())["username"] == "minty_test_bot"
    fake.say(42, "hello")
    updates = await bot.get_updates(None)
    assert updates[0]["message"]["text"] == "hello"
    assert await bot.get_updates(updates[0]["update_id"] + 1) == []
    msg = await bot.send_message(42, "hi" * 3000)  # long text is cut at Telegram's limit
    assert len(fake.sent[0]["text"]) == 4096 and msg["message_id"]
    await bot.send_message(42, "quiet", silent=True)
    assert fake.sent[1]["disable_notification"] is True and "disable_notification" not in fake.sent[0]
    await bot.edit_message(42, 5, "edited")
    await bot.typing(42)
    await bot.answer_callback("cb1", "ok")
    await bot.set_commands([("note", "Save a note")])
    await bot.send_poll(42, "Which?", ["a", "b"])
    assert fake.edits and fake.actions[0]["action"] == "typing" and fake.commands[0]["command"] == "note"
    assert fake.polls[0]["options"] == ["a", "b"] and fake.polls[0]["is_anonymous"] is False


async def test_a_429_is_waited_out_and_retried():
    fake = FakeTelegram()
    sleeps = []
    bot = api(fake, sleeps)
    fake.fail_once["sendMessage"] = {"status": 429}
    await bot.send_message(1, "x")
    assert sleeps == [1.0] and fake.calls.count("sendMessage") == 2 and len(fake.sent) == 1


async def test_errors_never_contain_the_token():
    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    bot = BotAPI(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    with pytest.raises(TelegramError) as e:
        await bot.send_message(1, "x")
    assert TOKEN not in str(e.value) and "network error (ConnectError)" in str(e.value)

    def refused(request):
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    bot = BotAPI(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(refused)))
    with pytest.raises(TelegramError) as e:
        await bot.get_me()
    assert e.value.code == 401 and TOKEN not in str(e.value)


def test_a_token_must_look_like_one():
    with pytest.raises(ValueError):
        BotAPI("not-a-token", httpx.AsyncClient())


# ---- W3-10: files and media ------------------------------------------------------------------------------------
async def test_a_file_is_fetched_by_id_and_downloaded():
    fake = FakeTelegram()
    fake.add_file("v1", b"OggS-voice-bytes", "voice/file_1.oga")
    bot = BotAPI(TOKEN, fake.client)
    info = await bot.get_file("v1")
    assert info["file_path"] == "voice/file_1.oga" and info["file_size"] == 16
    assert await bot.download(info["file_path"]) == b"OggS-voice-bytes"


async def test_a_download_over_the_cap_is_refused_and_a_failure_does_not_show_the_token():
    fake = FakeTelegram()
    fake.add_file("big", b"x" * 100, "documents/big.bin")
    bot = BotAPI(TOKEN, fake.client)
    with pytest.raises(TelegramError) as big:
        await bot.download("documents/big.bin", max_bytes=50)
    assert "too large" in str(big.value) and TOKEN not in str(big.value)
    with pytest.raises(TelegramError) as gone:
        await bot.download("documents/missing.bin")
    assert TOKEN not in str(gone.value)


async def test_voice_photo_and_document_go_up_as_multipart_files():
    fake = FakeTelegram()
    bot = BotAPI(TOKEN, fake.client)
    await bot.send_voice(7, b"OggS-reply", caption="hi")
    await bot.send_photo(7, b"\x89PNG-bytes", filename="page.png", caption="the page")
    await bot.send_document(7, b"%PDF-1.4", filename="notes.pdf")
    kinds = [(m["method"], m["chat_id"], m["filename"], m["data"]) for m in fake.media]
    assert kinds == [("sendVoice", 7, "voice.ogg", b"OggS-reply"), ("sendPhoto", 7, "page.png", b"\x89PNG-bytes"),
                     ("sendDocument", 7, "notes.pdf", b"%PDF-1.4")]
    assert fake.media[0]["caption"] == "hi"
