"""Real pictures in the terminal: which protocol the terminal speaks, a sixel encoder, the kitty commands, and drawing them
at a cell position without ever leaving junk behind."""
import base64
import re
import struct

import pytest

from minty_core.gfx import (ImageOverlay, Picture, cell_pixels, detect, encode_sixel, env_protocol, kitty_delete,
                            kitty_show, parse_reply)

ESC = "\x1b"


# -- which protocol ---------------------------------------------------------------------------------------------------

def test_a_kitty_answer_to_the_graphics_query_wins():
    assert parse_reply(b"\x1b_Gi=31;OK\x1b\\\x1b[?62;4;22c") == "kitty"


def test_the_device_attributes_with_a_4_mean_sixel():
    assert parse_reply(b"\x1b[?62;4;22c") == "sixel"
    assert parse_reply(b"\x1b[?65;1;4;9c") == "sixel"


def test_no_4_and_no_kitty_means_no_pictures():
    assert parse_reply(b"\x1b[?62;22c") is None and parse_reply(b"") is None and parse_reply(b"garbage") is None
    assert parse_reply(b"\x1b[?64;14c") is None  # 14 is not 4


def test_terminals_that_say_who_they_are_need_no_question():
    assert env_protocol({"TERM": "xterm-kitty"}) == "kitty"
    assert env_protocol({"KITTY_WINDOW_ID": "3"}) == "kitty"
    assert env_protocol({"TERM_PROGRAM": "ghostty"}) == "kitty"
    assert env_protocol({"TERM_PROGRAM": "WezTerm"}) == "kitty"
    assert env_protocol({"TERM": "xterm-256color", "VTE_VERSION": "8401"}) is None  # VTE: ask it


def test_detection_asks_once_and_a_dumb_or_silent_terminal_gets_no_pictures():
    asked = []

    def ask():
        asked.append(1)
        return b"\x1b[?62;4c"

    assert detect({"TERM": "xterm-256color"}, ask=ask, is_tty=True) == "sixel" and asked == [1]
    assert detect({"TERM": "xterm-kitty"}, ask=ask, is_tty=True) == "kitty" and asked == [1]  # said so: not asked again
    assert detect({"TERM": "dumb"}, ask=ask, is_tty=True) is None
    assert detect({"TERM": "xterm"}, ask=ask, is_tty=False) is None  # a pipe or a file
    assert detect({"TERM": "xterm"}, ask=lambda: b"", is_tty=True) is None  # never answered
    assert detect({"TERM": "xterm", "MINTY_IMAGES": "off"}, ask=ask, is_tty=True) is None


def test_the_cell_size_comes_from_the_window_size_or_a_guess():
    pixels = lambda fd, req, buf: struct.pack("HHHH", 40, 100, 900, 800)  # 100 columns in 900 px, 40 rows in 800 px
    assert cell_pixels(1, ioctl=pixels) == (9, 20)
    none = lambda fd, req, buf: struct.pack("HHHH", 40, 100, 0, 0)  # a terminal that does not say
    assert cell_pixels(1, ioctl=none) == (9, 18)

    def broken(fd, req, buf):
        raise OSError

    assert cell_pixels(1, ioctl=broken) == (9, 18)


# -- sixel ------------------------------------------------------------------------------------------------------------

def decode_sixel(text: str):
    """A small decoder, so the encoder is checked against what a terminal would draw: {(x, y): (r, g, b)} in 0..255."""
    assert text.startswith(f"{ESC}P") and text.endswith(f"{ESC}\\")
    body = text[text.index("q") + 1:-2]
    width, height = (int(v) for v in re.match(r'"1;1;(\d+);(\d+)', body).groups())
    palette, pixels, colour, x, band, i = {}, {}, 0, 0, 0, 0
    body = body[body.index("#"):]
    while i < len(body):
        c = body[i]
        if c == "#":
            m = re.match(r"#(\d+)(?:;2;(\d+);(\d+);(\d+))?", body[i:])
            colour = int(m.group(1))
            if m.group(2) is not None:
                palette[colour] = tuple(round(int(v) * 255 / 100) for v in m.groups()[1:])
            i += len(m.group(0))
            continue
        if c == "$":
            x, i = 0, i + 1
            continue
        if c == "-":
            x, band, i = 0, band + 1, i + 1
            continue
        count = 1
        if c == "!":
            m = re.match(r"!(\d+)(.)", body[i:])
            count, c = int(m.group(1)), m.group(2)
            i += len(m.group(0))
        else:
            i += 1
        bits = ord(c) - 63
        for _ in range(count):
            for row in range(6):
                if bits >> row & 1:
                    pixels[(x, band * 6 + row)] = palette[colour]
            x += 1
    return width, height, pixels


def test_a_two_colour_picture_round_trips_through_sixel():
    red, blue = (250, 10, 10), (10, 10, 250)
    w, h = 8, 12
    rgb = b"".join(bytes(red if y < 6 else blue) for y in range(h) for _ in range(w))
    out = encode_sixel(rgb, w, h, colors=16)
    width, height, pixels = decode_sixel(out)
    assert (width, height) == (8, 12) and len(pixels) == 8 * 12
    for (x, y), got in pixels.items():
        want = red if y < 6 else blue
        assert all(abs(a - b) <= 8 for a, b in zip(got, want)), (x, y, got)


def test_a_picture_that_is_not_a_multiple_of_six_rows_is_still_whole():
    rgb = bytes([200, 100, 50]) * (5 * 7)
    width, height, pixels = decode_sixel(encode_sixel(rgb, 5, 7, colors=4))
    assert (width, height) == (5, 7) and {y for _, y in pixels} == set(range(7))


def test_runs_are_compressed_and_the_palette_is_bounded():
    rgb = bytes([30, 60, 90]) * (64 * 12)  # one flat colour
    out = encode_sixel(rgb, 64, 12, colors=64)
    assert "!64" in out and len(out) < 400
    noise = bytes((i * 37) % 256 for i in range(40 * 40 * 3))
    assert len(set(re.findall(r"#(\d+);2;", encode_sixel(noise, 40, 40, colors=32)))) <= 32


# -- kitty ------------------------------------------------------------------------------------------------------------

def test_kitty_gets_the_picture_in_chunks_and_a_cell_size():
    png = bytes(range(256)) * 30  # 7.5 KB, 10 KB as base64: three chunks of at most 4096
    out = kitty_show(png, image_id=7, cols=16, rows=8)
    chunks = re.findall(rf"{ESC}_G([^;]*);([^{ESC}]*){ESC}\\", out)
    assert len(chunks) == 3
    first = dict(kv.split("=") for kv in chunks[0][0].split(","))
    assert first["a"] == "T" and first["f"] == "100" and first["i"] == "7" and first["c"] == "16" and first["r"] == "8"
    assert first["m"] == "1" and "m=1" in chunks[1][0] and chunks[2][0] == "m=0"
    assert base64.b64decode("".join(data for _, data in chunks)) == png
    assert first["z"] == "-1" and first["q"] == "2"  # behind the text, and no replies to read
    assert kitty_delete(7) == f"{ESC}_Ga=d,d=i,i=7,q=2{ESC}\\"


# -- drawing ----------------------------------------------------------------------------------------------------------

PIC = Picture(key="a", width=4, height=6, rgb=bytes([200, 30, 30]) * 24, png=b"PNGDATA")
PIC2 = Picture(key="b", width=4, height=6, rgb=bytes([30, 30, 200]) * 24, png=b"PNG2")


def overlay(protocol):
    out = []
    return ImageOverlay(out.append, protocol), out


def test_sixel_is_drawn_at_the_cell_and_the_cursor_goes_back():
    o, out = overlay("sixel")
    o.show(PIC, x=10, y=4, cols=2, rows=1)
    text = "".join(out)
    assert text.startswith(f"{ESC}7{ESC}[5;11H") and f"{ESC}P" in text and text.endswith(f"{ESC}8")  # 1-based cursor


def test_sixel_is_drawn_again_every_frame_because_the_text_paints_over_it():
    o, out = overlay("sixel")
    o.show(PIC, 1, 1, 2, 1)
    o.show(PIC, 1, 1, 2, 1)
    assert len(out) == 2


def test_kitty_sends_the_picture_once_and_only_moves_it_when_it_has_to():
    o, out = overlay("kitty")
    o.show(PIC, 10, 4, 16, 8)
    first = "".join(out)
    assert "a=T" in first and f"{ESC}[5;11H" in first
    out.clear()
    o.show(PIC, 10, 4, 16, 8)
    assert out == []  # nothing changed: nothing is sent
    o.show(PIC, 12, 4, 16, 8)  # the window was resized: it moves
    assert "".join(out).count("a=T") == 1
    out.clear()
    o.show(PIC2, 12, 4, 16, 8)  # a new cover replaces the old one
    text = "".join(out)
    assert "a=d" in text and "a=T" in text


def test_no_picture_removes_a_kitty_one_and_clear_leaves_nothing_behind():
    o, out = overlay("kitty")
    o.show(PIC, 1, 1, 4, 2)
    out.clear()
    o.show(None, 0, 0, 0, 0)
    assert "a=d" in "".join(out)
    o.show(PIC, 1, 1, 4, 2)
    out.clear()
    o.clear()
    assert "a=d" in "".join(out)
    out.clear()
    o.clear()
    assert out == []  # nothing left to remove


def test_the_pictures_are_never_drawn_without_a_protocol():
    o, out = overlay(None)
    o.show(PIC, 1, 1, 2, 1)
    o.clear()
    assert out == []
