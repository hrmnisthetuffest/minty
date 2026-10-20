"""679 the daily summary card, 683 the daily tip, and the Telegram delivery."""
import shutil
import struct
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from minty_core.config import Config
from minty_core.selfcheck.card import CardData, card_svg, gather, render_png
from minty_core.selfcheck.tips import TIPS, TipBook
from minty_core.system.runner import Result

TODAY = date(2026, 10, 18)


# ---- tips ----------------------------------------------------------------------------------------------------------------
def test_every_tip_names_a_real_switch_or_none_and_is_short():
    cfg = Config()
    for flag, text in TIPS:
        assert 20 <= len(text) <= 160, text
        if flag:
            node = cfg
            for part in flag.split("."):
                assert hasattr(node, part), flag
                node = getattr(node, part)
    assert len(TIPS) >= 25 and len({t for _, t in TIPS}) == len(TIPS)


def test_a_tip_is_about_something_that_is_switched_on_and_never_repeats_within_two_weeks(tmp_path):
    cfg = Config()
    book = TipBook(tmp_path / "tips.json")
    general = {text for flag, text in TIPS if flag is None}
    seen = []
    for i in range(14):
        seen.append(book.pick(cfg, TODAY + timedelta(days=i)))
    assert set(seen) <= general and len(set(seen)) == 14  # nothing mentions a feature that is off
    cfg.study.enabled = True
    cfg.vault.enabled = True
    on = {text for flag, text in TIPS if flag in (None, "study.enabled", "vault.enabled")}
    later = TipBook(tmp_path / "tips2.json")
    got = {later.pick(cfg, TODAY + timedelta(days=i)) for i in range(20)}
    assert got <= on and any("quiz" in t.lower() for t in got)


def test_the_same_day_gives_the_same_tip_and_the_history_survives_a_restart(tmp_path):
    cfg = Config()
    a = TipBook(tmp_path / "t.json").pick(cfg, TODAY)
    assert TipBook(tmp_path / "t.json").pick(cfg, TODAY) == a
    nxt = TipBook(tmp_path / "t.json").pick(cfg, TODAY + timedelta(days=1))
    assert nxt != a


# ---- the card ------------------------------------------------------------------------------------------------------------
DATA = CardData(day=TODAY, done=12, refused=1, jobs=2, habits_done=3, habits_total=5, tasks_done=4, events=3,
                tip="Say 'quiz me' for a flashcard round of what you studied.")


def test_the_card_is_valid_svg_with_the_numbers_and_the_tip_and_nothing_executable():
    svg = card_svg(DATA)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and root.attrib["viewBox"] == "0 0 1080 1080"
    text = "".join(root.itertext())
    for needle in ("Sunday 18 Oct", "12", "3/5", "quiz me"):
        assert needle in text
    assert "<script" not in svg.lower() and "onload" not in svg.lower() and "href=" not in svg.lower()


def test_the_tip_text_is_escaped_and_wrapped():
    svg = card_svg(CardData(day=TODAY, tip="<script>alert(1)</script> & a very long tip " * 4))
    ET.fromstring(svg)  # still well-formed
    assert "<script" not in svg.lower().replace("&lt;script&gt;", "") and "&amp;" in svg


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", data[16:24])


@pytest.mark.skipif(shutil.which("rsvg-convert") is None, reason="no rsvg-convert")
async def test_the_svg_becomes_a_png_with_rsvg(tmp_path):
    out = await render_png(card_svg(DATA), tmp_path / "cards", "card-20261018", prefer="rsvg")
    assert out is not None and out.suffix == ".png" and png_size(out.read_bytes()) == (1080, 1080)
    assert oct(out.stat().st_mode & 0o777) == "0o600"


@pytest.mark.skipif(shutil.which("google-chrome-stable") is None, reason="no Chrome")
async def test_or_through_headless_chrome_when_that_is_what_there_is(tmp_path):
    out = await render_png(card_svg(DATA), tmp_path / "cards", "card-20261019", prefer="chrome")
    assert out is not None and png_size(out.read_bytes()) == (1080, 1080)


async def test_with_no_renderer_the_svg_is_kept_and_none_is_returned(tmp_path):
    class Nothing:
        def have(self, name):
            return False

        async def run(self, argv, timeout=None, cwd=None):
            return Result(127, missing=True)

    out = await render_png(card_svg(DATA), tmp_path / "cards", "card-x", runner=Nothing())
    assert out is None and (tmp_path / "cards" / "card-x.svg").is_file()


def test_the_card_counts_things_and_carries_no_private_text(tmp_path):
    from minty_core.receipts import ReceiptStore

    store = ReceiptStore(tmp_path / "r.db", clock=lambda: datetime(2026, 10, 18, 12).timestamp())
    store.record("files_write", 1, "Saved my diary entry about Ravi.txt")
    store.record("mail_send", 2, "Sent the application to the school", outcome="ok")
    store.record("files_delete", 3, "Deleted old.txt", outcome="declined")
    ctx_services = {"receipts": store}
    d = gather(ctx_services, Config(), TODAY, tip="A tip.", now=datetime(2026, 10, 18, 21, 0))
    assert (d.done, d.refused) == (2, 1)
    svg = card_svg(d)
    assert "diary" not in svg and "Ravi" not in svg and "application" not in svg


# ---- telegram -------------------------------------------------------------------------------------------------------------
async def test_the_card_goes_to_the_phone_only_when_switched_on_never_on_a_private_day_and_only_if_it_is_a_card(tmp_path):
    from tests.test_telegram_gateway import OWNER, build
    from minty_core.telegram.cards import CardRelay

    gw, fake, store, _ = build(tmp_path)
    folder = tmp_path / "cards"
    folder.mkdir()
    png = folder / "card-20261018.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    other = tmp_path / "other.png"
    other.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    relay = CardRelay(gw, root=lambda: folder)
    assert await relay.handle({"path": str(png)}) == "off"
    data = store.config.model_dump(mode="json")
    data["selfcheck"] = {**data["selfcheck"], "card_push": True}
    store.update(data)
    assert await relay.handle({"path": str(other)}) == "refused"  # not inside the cards folder
    assert await relay.handle({"path": str(folder / "missing.png")}) == "refused"
    assert await relay.handle({"path": str(png), "caption": "Your day"}) == "sent"
    sent = fake.media[0]
    assert sent["method"] == "sendPhoto" and sent["chat_id"] == OWNER and sent["caption"] == "Your day"
    gw2, fake2, store2, _ = build(tmp_path / "p", private=True)
    data = store2.config.model_dump(mode="json")
    data["selfcheck"] = {**data["selfcheck"], "card_push": True}
    store2.update(data)
    assert await CardRelay(gw2, root=lambda: folder).handle({"path": str(png)}) == "private" and fake2.media == []
