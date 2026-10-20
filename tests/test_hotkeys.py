"""Two buttons on the display: mute (default key M) and start listening (default key Space). They are keys and mouse clicks,
both can be changed in the settings, and muting turns the whole display red."""
import asyncio
import io

import pytest
from pydantic import ValidationError
from rich.console import Console

from minty_core.config import Config
from minty_core.tui import (MUTED_RGB, Hotkeys, Mouse, TuiModel, _KeyReader, click_action, dispatch_key, parse_keys, render)

KEYS = Hotkeys(mute="m", listen=" ", enabled=True)


def lines(model, width=100, height=34):
    console = Console(width=width, height=height, file=io.StringIO(), color_system=None, force_terminal=False, record=True)
    console.print(render(model, width, height))
    return console.export_text().split("\n")


# ---- the settings ------------------------------------------------------------------------------------------
def test_the_defaults_are_m_and_space_and_everything_is_on():
    c = Config()
    assert (c.terminal_mute_key, c.terminal_listen_key, c.terminal_hotkeys, c.terminal_mouse) == ("m", "space", True, True)


@pytest.mark.parametrize("bad", ["", "ab", "enter", "  ", "\t"])
def test_a_key_must_be_one_character_or_the_word_space(bad):
    with pytest.raises(ValidationError):
        Config(terminal_mute_key=bad)


def test_keys_are_lower_cased_and_must_differ():
    assert Config(terminal_mute_key="X").terminal_mute_key == "x"
    with pytest.raises(ValidationError):
        Config(terminal_mute_key="k", terminal_listen_key="k")


def test_hotkeys_come_from_the_config():
    k = Hotkeys.from_config(Config(terminal_mute_key="x", terminal_listen_key="space", terminal_hotkeys=False))
    assert (k.mute, k.listen, k.enabled) == ("x", " ", False)


# ---- keys ----------------------------------------------------------------------------------------------------
def test_m_mutes_and_space_listens_when_nothing_is_being_typed():
    m = TuiModel()
    assert dispatch_key(m, "m", KEYS) == ("mute", None) and dispatch_key(m, " ", KEYS) == ("listen", None)
    assert m.buffer == ""


def test_other_letters_start_typing_as_before():
    m = TuiModel()
    for ch in "hi":
        assert dispatch_key(m, ch, KEYS) is None
    assert m.buffer == "hi"
    assert dispatch_key(m, "\r", KEYS) == ("send", "hi")


def test_m_and_space_are_ordinary_letters_once_you_are_typing():
    m = TuiModel()
    for ch in "hello m":
        dispatch_key(m, ch, KEYS)
    assert m.buffer == "hello m"


def test_enter_opens_a_line_so_a_message_can_start_with_m():
    m = TuiModel()
    assert dispatch_key(m, "\r", KEYS) is None and m.composing
    for ch in "make tea":
        assert dispatch_key(m, ch, KEYS) is None
    assert m.buffer == "make tea"
    assert dispatch_key(m, "\r", KEYS) == ("send", "make tea") and not m.composing
    dispatch_key(m, "\r", KEYS)  # open again
    assert dispatch_key(m, "\r", KEYS) is None and not m.composing  # Enter on an empty line closes it


def test_backspace_on_an_empty_line_closes_it():
    m = TuiModel()
    dispatch_key(m, "\r", KEYS)
    dispatch_key(m, "\x7f", KEYS)
    assert not m.composing and dispatch_key(m, "m", KEYS) == ("mute", None)


def test_with_hotkeys_off_every_key_types():
    m = TuiModel()
    off = Hotkeys("m", " ", False)
    for ch in "m x":
        assert dispatch_key(m, ch, off) is None
    assert m.buffer == "m x"


def test_other_keys_can_be_chosen():
    m = TuiModel()
    k = Hotkeys("x", "l", True)
    assert dispatch_key(m, "x", k) == ("mute", None) and dispatch_key(m, "l", k) == ("listen", None)
    assert dispatch_key(m, "m", k) is None and m.buffer == "m"


# ---- the mouse -----------------------------------------------------------------------------------------------
def test_mouse_reports_are_read_and_arrow_keys_are_still_dropped():
    assert parse_keys("\x1b[<0;70;32M") == [Mouse(0, 70, 32, True)]
    assert parse_keys("\x1b[<0;70;32m") == [Mouse(0, 70, 32, False)]
    assert parse_keys("a\x1b[A\x1b[<64;5;5Mb") == ["a", Mouse(64, 5, 5, True), "b"]


def test_both_buttons_are_drawn_on_the_chat_bar_and_each_click_finds_its_own():
    m = TuiModel()
    out = lines(m)
    top = next(i for i, line in enumerate(out) if "Mute" in line and "Listen" in line)
    assert top == 34 - 3  # the top edge of the chat bar
    mute, listen = m.buttons["mute"], m.buttons["listen"]
    assert out[top][mute[0]:mute[1]].strip() == "M Mute" and out[top][listen[0]:listen[1]].strip() == "Space Listen"
    y = top + 1  # the mouse counts rows from 1
    assert click_action(m, Mouse(0, mute[0] + 2, y, True)) == "mute"
    assert click_action(m, Mouse(0, listen[0] + 2, y, True)) == "listen"


@pytest.mark.parametrize("event", [Mouse(0, 5, 32, True), Mouse(0, 5, 1, True), Mouse(0, 5, 31, True), Mouse(64, 90, 32, True)])
def test_clicks_elsewhere_do_nothing(event):
    m = TuiModel()
    lines(m)
    assert click_action(m, event) is None


def test_only_the_press_of_the_left_button_counts():
    m = TuiModel()
    lines(m)
    x, y = m.buttons["mute"][0] + 2, 34 - 3 + 1
    assert click_action(m, Mouse(0, x, y, True)) == "mute"
    assert click_action(m, Mouse(0, x, y, False)) is None  # the release
    assert click_action(m, Mouse(2, x, y, True)) is None  # the right button


def test_the_buttons_name_the_chosen_keys():
    m = TuiModel()
    m.hotkeys = Hotkeys("x", "l", True)
    top = next(line for line in lines(m) if "Mute" in line)
    assert "X Mute" in top and "L Listen" in top


def test_no_buttons_when_hotkeys_and_mouse_are_both_off():
    m = TuiModel()
    m.hotkeys = Hotkeys("m", " ", False)
    m.mouse = False
    assert not any("Mute" in line for line in lines(m)) and m.buttons == {}


# ---- muted looks red -------------------------------------------------------------------------------------------
def test_muting_turns_the_accent_red_and_says_so_on_the_button():
    m = TuiModel(status=lambda: {"muted": True})
    m.reduced_motion = True
    out = lines(m)
    assert tuple(round(c) for c in m.accent_rgb) == MUTED_RGB
    assert any("M Muted" in line for line in out) and any("Press M to unmute" in line for line in out)
    quiet = TuiModel(status=lambda: {"muted": False})
    quiet.reduced_motion = True
    lines(quiet)
    assert tuple(round(c) for c in quiet.accent_rgb) != MUTED_RGB
    assert any("M Mute " in line and "Muted" not in line for line in lines(quiet))


def test_the_accent_fades_back_when_unmuted():
    state = {"muted": True}
    m = TuiModel(status=lambda: dict(state))
    m.reduced_motion = True
    lines(m)
    state["muted"] = False
    lines(m)
    assert tuple(round(c) for c in m.accent_rgb) != MUTED_RGB


# ---- the reader ---------------------------------------------------------------------------------------------
async def test_the_reader_turns_keys_clicks_and_typing_into_actions():
    m = TuiModel()
    lines(m)
    got, sent = [], []

    async def send(text):
        sent.append(text)

    reader = _KeyReader(m, send, None, on_action=got.append, hotkeys=lambda: KEYS, mouse=lambda: True)
    reader.feed("m ")
    assert got == ["mute", "listen"]
    reader.feed("hi\r")
    await asyncio.sleep(0)
    assert sent == ["hi"]
    x, y = m.buttons["listen"][0] + 2, 34 - 3 + 1
    reader.feed(f"\x1b[<0;{x};{y}M")
    assert got[-1] == "listen"


def test_the_buttons_mute_and_listen_through_the_pipeline():
    from types import SimpleNamespace as NS

    from minty_core.app import _button_action

    pipe = NS(muted=False, started=0)
    pipe.listen_now = lambda: setattr(pipe, "started", pipe.started + 1)
    act = _button_action(NS(pipeline=pipe))
    act("mute")
    assert pipe.muted is True
    act("mute")
    assert pipe.muted is False
    act("listen")
    assert pipe.started == 1
    _button_action(NS(pipeline=None))("mute")  # before the voice pipeline exists: nothing happens, nothing breaks
