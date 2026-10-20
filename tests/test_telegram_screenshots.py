"""Browser screenshots to the phone: off by default, checked locally, never a file from elsewhere (W3-14)."""
import asyncio
from datetime import datetime
from pathlib import Path

from tests.test_telegram_gateway import OWNER, build
from minty_core.events import EventBus
from minty_core.secretscan import SecretScanner
from minty_core.telegram.push import PushRouter
from minty_core.telegram.screenshots import ScreenshotRelay

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
SECRET = "gsk_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def setup(tmp_path, ocr_text="A shop page. Total 499 rupees.", enabled=True, unchecked=False, quiet=False,
          private=False):
    gw, fake, store, _ = build(tmp_path, private=private)
    data = store.config.model_dump(mode="json")
    data["telegram"]["screenshots"], data["telegram"]["screenshots_unchecked"] = enabled, unchecked
    store.update(data)
    folder = tmp_path / "ws"
    folder.mkdir()
    shot = folder / "screenshot-1.png"
    shot.write_bytes(PNG)
    bus = EventBus()
    push = PushRouter(gw, bus, quiet=lambda now: quiet, state_path=tmp_path / "held.json",
                      clock=lambda: datetime(2026, 10, 15, 12, 0))

    async def ocr(path):
        return ocr_text

    relay = ScreenshotRelay(gw, SecretScanner(), ocr=ocr if ocr_text is not None else None, root=lambda: folder,
                            push=push)
    return gw, fake, relay, shot, push


def event(path, **kw):
    return {"type": "screenshot", "path": str(path), "url": "https://shop.example.org/cart?id=3", **kw}


async def test_it_is_off_until_you_switch_it_on(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path, enabled=False)
    assert await relay.handle(event(shot)) == "off"
    assert fake.media == [] and fake.sent == []


async def test_a_clean_screenshot_is_sent_with_only_the_site_name(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path)
    assert await relay.handle(event(shot)) == "sent"
    sent = fake.media[0]
    assert sent["method"] == "sendPhoto" and sent["chat_id"] == OWNER and sent["data"] == PNG
    assert sent["caption"] == "[laptop] shop.example.org" and "cart" not in sent["caption"]


async def test_a_screenshot_showing_a_secret_is_withheld_and_the_notice_does_not_repeat_it(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path, ocr_text=f"Settings\nAPI key: {SECRET}")
    assert await relay.handle(event(shot)) == "withheld"
    assert fake.media == [] and SECRET not in "\n".join(fake.texts(OWNER))
    assert "groq key" in fake.texts(OWNER)[-1] and shot.name in fake.texts(OWNER)[-1]


async def test_with_no_reader_it_is_not_sent_unless_you_said_unchecked_is_fine(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path, ocr_text=None)
    assert await relay.handle(event(shot)) == "unchecked" and fake.media == []
    assert "can't check" in fake.texts(OWNER)[-1]
    gw2, fake2, relay2, shot2, _ = setup(tmp_path / "u", ocr_text=None, unchecked=True)
    assert await relay2.handle(event(shot2)) == "sent" and len(fake2.media) == 1


async def test_a_private_screenshot_or_private_day_sends_nothing_at_all(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path)
    assert await relay.handle(event(shot, private=True)) == "private"
    gw2, fake2, relay2, shot2, _ = setup(tmp_path / "p", private=True)
    assert await relay2.handle(event(shot2)) == "private"
    assert fake.media == fake.sent == fake2.media == fake2.sent == []


async def test_quiet_hours_hold_the_picture_and_say_so_once_afterwards(tmp_path):
    gw, fake, relay, shot, push = setup(tmp_path, quiet=True)
    assert await relay.handle(event(shot)) == "held"
    assert fake.media == [] and fake.sent == []
    assert len(push._held) == 1


async def test_a_file_that_is_not_in_the_workspace_or_not_a_picture_is_never_sent(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path)
    secret_file = tmp_path / "notes.txt"
    secret_file.write_text("my diary")
    assert await relay.handle(event(secret_file)) == "refused"
    link = tmp_path / "ws" / "screenshot-2.png"
    link.symlink_to(secret_file)
    assert await relay.handle(event(link)) == "refused"
    fake_png = tmp_path / "ws" / "shot.png"
    fake_png.write_text("not an image")
    assert await relay.handle(event(fake_png)) == "refused"
    assert await relay.handle(event(Path("/etc/passwd"))) == "refused"
    assert fake.media == []


async def test_the_bus_loop_picks_screenshot_events_up(tmp_path):
    gw, fake, relay, shot, _ = setup(tmp_path)
    bus = EventBus()
    task = asyncio.create_task(relay.run(bus))
    await asyncio.sleep(0)
    bus.publish("screenshot", path=str(shot), url="https://a.test/")
    for _ in range(100):
        if fake.media:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(fake.media) == 1
