"""The phone sees what it is being asked to approve: the draft video, sent only from the studio folder to the paired chats."""
import shutil
import subprocess

import pytest

from minty_core.telegram.previews import PreviewRelay
from tests.test_telegram_gateway import OWNER, build

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def relay(gw, folder, on=True):
    return PreviewRelay(gw, root=lambda: folder, enabled=lambda: on)


async def test_a_draft_video_goes_to_the_paired_chat_as_a_video(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    folder = tmp_path / "studio"
    (folder / "work").mkdir(parents=True)
    video = folder / "work" / "reel.mp4"
    video.write_bytes(MP4)
    assert await relay(gw, folder).handle({"path": str(video), "caption": "Draft 3: Minecraft parkour"}) == "sent"
    sent = fake.media[0]
    assert sent["method"] == "sendVideo" and sent["chat_id"] == OWNER and sent["caption"] == "Draft 3: Minecraft parkour"
    assert sent["data"] == MP4


async def test_pictures_go_as_photos_and_nothing_else_is_ever_sent(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    folder = tmp_path / "studio"
    folder.mkdir()
    png = folder / "slide.png"
    png.write_bytes(PNG)
    assert await relay(gw, folder).handle({"path": str(png)}) == "sent" and fake.media[0]["method"] == "sendPhoto"
    secret = tmp_path / "id_rsa.mp4"  # outside the studio folder
    secret.write_bytes(MP4)
    inside_text = folder / "notes.txt"
    inside_text.write_text("secret")
    fake_video = folder / "fake.mp4"
    fake_video.write_bytes(b"this is not a video")
    link = folder / "link.mp4"
    link.symlink_to(secret)
    r = relay(gw, folder)
    for path in (secret, inside_text, fake_video, link, folder / "missing.mp4", "/etc/passwd"):
        assert await r.handle({"path": str(path)}) == "refused", path
    assert len(fake.media) == 1


async def test_it_is_off_until_switched_on_and_never_on_a_private_day(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    folder = tmp_path / "studio"
    folder.mkdir()
    video = folder / "reel.mp4"
    video.write_bytes(MP4)
    assert await relay(gw, folder, on=False).handle({"path": str(video)}) == "off"
    gw2, fake2, _, _ = build(tmp_path / "p", private=True)
    assert await relay(gw2, folder).handle({"path": str(video)}) == "private" and fake2.media == []
    assert fake.media == []


async def test_a_file_too_big_for_telegram_is_not_sent(tmp_path, monkeypatch):
    gw, fake, _, _ = build(tmp_path)
    folder = tmp_path / "studio"
    folder.mkdir()
    video = folder / "big.mp4"
    video.write_bytes(MP4)
    monkeypatch.setattr("minty_core.telegram.previews.MAX_BYTES", 100)
    assert await relay(gw, folder).handle({"path": str(video)}) == "refused" and fake.media == []
