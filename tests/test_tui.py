import re
import io

from rich.console import Console

from minty_core.tui import TuiModel, meter, orb, render, status_items, wave, working


def text_of(renderable, width=80, height=30):
    console = Console(file=io.StringIO(), width=width, height=height, force_terminal=False)
    console.print(renderable)
    return console.file.getvalue()


def test_model_follows_events():
    m = TuiModel()
    m.apply({"type": "state", "state": "listening"})
    m.apply({"type": "level", "db": -20.0, "threshold_db": -38.0, "speech": True})
    m.apply({"type": "partial", "text": "what is the"})
    assert (m.state, m.db, m.speech, m.caption) == ("listening", -20.0, True, "what is the")
    m.apply({"type": "transcript", "role": "user", "text": "what is the time"})
    m.apply({"type": "transcript", "role": "assistant", "text": "It is noon."})
    assert m.caption == "" and list(m.turns)[-1] == ("assistant", "It is noon.")


def test_render_shows_state_conversation_and_display():
    m = TuiModel(flags=lambda: {"live": True, "private": False})
    m.apply({"type": "state", "state": "speaking"})
    m.apply({"type": "transcript", "role": "user", "text": "show me my todo list"})
    m.apply({"type": "display", "title": "Todo", "content": "- buy milk\n- call mum"})
    out = text_of(render(m, 80, 30, now=1.0))
    assert "SPEAKING" in out and "[live]" in out and "[private]" not in out
    assert "show me my todo list" in out and "buy milk" in out and "Todo" in out


def test_wave_and_meter_are_exact_width_and_react_to_level():
    m = TuiModel()
    m.state = "listening"
    quiet = wave(m, 40, 1.0)
    m.db = -10.0
    for _ in range(20):
        loud = wave(m, 40, 1.0)
    assert len(quiet.plain) == 40 and len(loud.plain) == 40
    assert loud.plain.count(" ") < quiet.plain.count(" ")
    m.speech = True
    assert "voice" in meter(m, 30).plain and "-10 dB" in meter(m, 30).plain


def test_display_clear_event_removes_the_pane():
    m = TuiModel()
    m.apply({"type": "display", "title": "x", "content": "y"})
    m.apply({"type": "display", "title": "", "content": ""})
    assert "Display" not in text_of(render(m, 80, 30, now=0.0))








def test_full_render_includes_chips_and_working_panels():
    m = TuiModel(status=lambda: {"voice_mode": "live", "tier": "fast", "subagents": [{"id": 2, "what": "research", "secs": 3}],
                                 "jobs": [], "brain_ready": 1, "brain_total": 1})
    out = text_of(render(m, 100, 34, now=1.0), width=100, height=34)
    assert "Gemini" in out and "Working" in out and "research" in out


def _look(text):
    """Shape and colours together: two orbs match only if they look identical."""
    return text.plain, [str(span.style) for span in text.spans]


def test_orb_is_a_fixed_size_smooth_animation_per_state():
    from minty_core.tui import ORB_COLORS, orb
    model = TuiModel()
    looks = {}
    for state in ORB_COLORS:
        model.state = state
        first, later = orb(model, 1.0), orb(model, 1.5)
        lines = first.plain.split("\n")
        assert len(lines) == 6 and all(len(line) == 12 for line in lines)
        assert set(first.plain.replace("\n", "")) <= set(" \u2580\u2584")
        assert first.plain.strip(), state
        looks[state] = _look(first)
        if state not in ("listening", "error"):  # listening follows the mic; an error is shown still
            assert _look(first) != _look(later), f"{state} should move"
    assert len({str(v) for v in looks.values()}) == len(looks)  # every state looks different


def test_orb_glow_is_smooth_not_on_off():
    from minty_core.tui import orb
    model = TuiModel()
    model.state = "listening"
    model.level = 0.5
    tones = {str(span.style) for span in orb(model, 1.0).spans}
    assert len(tones) > 20  # a gradient, not a handful of flat colours


def test_listening_orb_grows_with_the_voice():
    from minty_core.tui import orb
    model = TuiModel()
    model.state = "listening"

    def brightness():
        total = 0
        for span in orb(model, 1.0).spans:
            total += sum(int(n) for n in re.findall(r"\d+", str(span.style)))
        return total

    quiet = brightness()
    model.level = 0.9
    assert brightness() > quiet


def test_orb_shows_on_wide_terminals_only():
    from rich.console import Console
    model = TuiModel()
    model.state = "thinking"
    for width, has_orb in ((100, True), (50, False)):
        console = Console(width=width, height=30, record=True)
        console.print(render(model, width, 30, now=2.0))
        text = console.export_text()
        assert ("\u2580" in text or "\u2584" in text) is has_orb


def test_working_panel_shows_job_kinds_progress_and_questions():
    st = {"work": [
        {"id": 3, "kind": "browser", "what": "find the cheapest lamp", "secs": 65, "status": "running",
         "progress": "clicking Sort by", "question": ""},
        {"id": 4, "kind": "browser", "what": "check homework", "secs": 5, "status": "needs_user",
         "progress": "", "question": "Please log in to SchoolPad"},
        {"id": 5, "kind": "think", "what": "is the plan sound", "secs": 2, "status": "queued", "progress": "",
         "question": ""}]}
    text = working(st).plain
    assert "browser #3  find the cheapest lamp  1m05s  — clicking Sort by" in text
    assert "? browser #4" in text and "Please log in to SchoolPad" in text and "… deep think #5" in text
    items = {label: value for label, value, _ in status_items({**st, "voice_mode": "classic"})}
    assert items["WORKING"] == "2 browser · 1 deep think · 1 need you"


def test_redraw_rate_follows_activity_and_reduced_motion():
    from minty_core.tui import ACTIVE_FPS, IDLE_FPS, STILL_FPS, frame_delay
    m = TuiModel()
    m.state = "idle"
    assert frame_delay(m) == 1 / IDLE_FPS and frame_delay(m, reduced_motion=True) == 1 / STILL_FPS
    for state in ("listening", "processing", "thinking", "executing", "speaking"):
        m.state = state
        assert frame_delay(m) == 1 / ACTIVE_FPS
    m.state = "idle"
    m.apply({"type": "partial", "text": "hello", "tentative": ""})
    assert frame_delay(m) == 1 / ACTIVE_FPS  # words are still appearing


def test_processing_and_executing_have_their_own_calm_look_and_label():
    m = TuiModel()
    console = Console(width=100, height=30, record=True, file=io.StringIO())
    for state, label in (("processing", "WORKING ON IT"), ("executing", "RUNNING A TOOL"),
                         ("thinking", "THINKING")):
        m.state = state
        console.print(render(m, 100, 30, now=1.0))
        assert label in console.export_text()
    m.state = "processing"
    still = orb(m, 1.0).plain
    assert orb(m, 1.0).plain == still  # deterministic for a given time


def test_reduced_motion_freezes_shapes():
    m = TuiModel()
    m.state, m.reduced_motion = "thinking", True
    console = Console(width=100, height=30, record=True, file=io.StringIO())
    console.print(render(m, 100, 30, now=1.0))
    first = console.export_text()
    console.print(render(m, 100, 30, now=7.3))
    assert console.export_text() == first


def test_title_shows_the_version():
    import minty_core
    out = text_of(render(TuiModel(), 80, 30, now=1.0))
    assert f"Minty v{minty_core.__version__}" in out


def test_version_flag_prints_the_version(capsys):
    import pytest
    import minty_core
    from minty_core.app import parse_args
    with pytest.raises(SystemExit) as stop:
        parse_args(["--version"])
    assert stop.value.code == 0
    assert minty_core.__version__ in capsys.readouterr().out


# -- the redesign: calm always-on display ------------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

FULL_STATUS = {"voice_mode": "classic", "tier": "fast", "private": False, "music": True, "muted": False, "tts": "kokoro",
               "wake": "name", "brain_ready": 9, "brain_total": 9, "cooling": [], "context_msgs": 0, "rules": 12,
               "commands": 2, "subagents": [], "jobs": [], "model": "claude · haiku", "picked": "claude",
               "models": ["claude", "groq", "gemini"],
               "capacity": {"share": 0.38, "calls_left": 113, "forecast": "lasts until the daily reset"},
               "now_playing": {"title": "Veridis Quo", "artist": "Daft Punk", "album": "Discovery", "status": "Playing",
                               "player": "spotify", "length_s": 345, "position_s": 60.0}}
WALL = datetime(2026, 10, 19, 22, 47)


def look(m, width=110, height=36):
    return text_of(render(m, width, height, now=1.0, wall=WALL), width=width, height=height)


def test_the_stage_in_the_title_is_red_and_the_rest_is_not():
    import minty_core
    from minty_core.tui import title_text
    t = title_text()
    assert t.plain == f"Minty v{minty_core.__version__}"
    stage = minty_core.__version__.partition("-")[2]  # a plain release such as 1.0.1 has no stage to show in red
    reds = [t.plain[s.start:s.end] for s in t.spans if "e5484d" in str(s.style)]
    assert reds == ([stage] if stage else [])


def test_the_bottom_line_is_gone_and_a_clean_self_check_never_shows():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "notice", "message": "Self-check: 9/9 models keyed · speech local · problems: none"})
    out = look(m)
    assert "Self-check" not in out and m.active_notice() == ""
    assert out.rstrip().splitlines()[-1].strip().startswith(("╰", "└")) or "Conversation" in out  # panels end the screen


def test_a_real_problem_shows_briefly_inside_the_header_then_fades():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "notice", "message": "Self-check: 0/9 models keyed · problems: no model has a key"})
    assert "no model has a key" in look(m)
    m.notice_at -= 60  # a minute later
    assert "no model has a key" not in look(m)
    m.apply({"type": "error", "message": "speech recognition failed"})
    assert "speech recognition failed" in look(m)




def test_the_music_card_sits_beside_the_conversation_and_shows_the_track_and_progress():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "transcript", "role": "user", "text": "what time is it"})
    out = look(m)
    for expected in ("NOW PLAYING", "Veridis Quo", "Daft Punk", "1:00", "5:45", "what time is it"):
        assert expected in out, expected
    paused = {**FULL_STATUS, "now_playing": {**FULL_STATUS["now_playing"], "status": "Paused"}}
    assert "paused" in look(TuiModel(status=lambda: paused)).lower()
    nothing = {**FULL_STATUS, "music": False, "now_playing": None}
    assert "Nothing playing" in look(TuiModel(status=lambda: nothing))
    sink_only = {**FULL_STATUS, "now_playing": None}
    assert "Playing" in look(TuiModel(status=lambda: sink_only))  # a sound is playing, no track details


def test_a_narrow_screen_drops_the_card_not_the_conversation():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "transcript", "role": "user", "text": "what time is it"})
    out = look(m, width=70)
    assert "NOW PLAYING" not in out and "what time is it" in out


def test_noting_mode_lists_the_notes_in_the_conversation_panel_until_it_ends():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "transcript", "role": "user", "text": "earlier chat"})
    m.apply({"type": "notes", "active": True, "items": ["buy milk", "call dad at five"]})
    out = look(m)
    assert "Noting" in out and "buy milk" in out and "call dad at five" in out
    m.apply({"type": "notes", "active": False, "items": ["buy milk", "call dad at five"]})
    out = look(m)
    assert "buy milk" not in out and "earlier chat" in out


def test_the_clock_and_date_show_on_wide_screens_only():
    from minty_core.tui import CLOCK_BLOCK_W, CLOCK_ROWS, clock_block
    rows = clock_block(WALL)
    assert len(rows) == CLOCK_ROWS and {len(r) for r in rows} == {CLOCK_BLOCK_W}  # equal-width rows of solid pixels
    assert clock_block(WALL, colon=False) != rows and len({len(r) for r in clock_block(WALL, colon=False)}) == 1  # the colon blinks
    wide = look(TuiModel(status=lambda: FULL_STATUS), width=130)
    assert "M O N" in wide and "1 9" in wide and "O C T" in wide
    assert "M O N" not in look(TuiModel(status=lambda: FULL_STATUS), width=70)


def test_the_clock_is_twelve_hours_with_am_or_pm():
    from minty_core.tui import clock_block, clock_text
    night = clock_text(WALL, (255, 180, 60), True, False).plain  # 22:47
    assert "PM" in night and "AM" not in night
    morning = clock_text(WALL.replace(hour=7, minute=5), (255, 180, 60), True, False).plain
    assert "AM" in morning and "PM" not in morning
    midnight = clock_text(WALL.replace(hour=0, minute=0), (255, 180, 60), True, False).plain
    assert "AM" in midnight
    # 22:47 reads 10:47: the digits are those of 10 and 47, not 22
    assert clock_block(WALL) == clock_block(WALL.replace(hour=10))
    assert clock_block(WALL.replace(hour=13)) == clock_block(WALL.replace(hour=1))
    # a one-digit hour keeps the clock steady: the first digit is blank and the rest does not move
    one = clock_block(WALL.replace(hour=7))
    two = clock_block(WALL.replace(hour=10))
    assert {len(r) for r in one} == {len(r) for r in two}
    assert all(row[:9].strip() == "" for row in one)
    assert clock_block(WALL.replace(hour=0)) == clock_block(WALL.replace(hour=12))  # midnight reads 12


def test_the_marker_sits_beside_the_digits_and_the_clock_still_fits():
    from minty_core.tui import CLOCK_BLOCK_W, CLOCK_W, clock_text
    assert CLOCK_W > CLOCK_BLOCK_W
    for row in clock_text(WALL, (255, 180, 60), True, False).plain.splitlines()[:-1]:
        assert len(row) <= CLOCK_W


def test_nothing_overflows_at_any_width():
    m = TuiModel(status=lambda: FULL_STATUS, flags=lambda: {"live": False, "private": False})
    for who, what in (("user", "what is the weather like in Paris today and tomorrow " * 4),
                      ("assistant", "It is sunny and warm all week, with a light breeze in the evening. " * 4)):
        m.apply({"type": "transcript", "role": who, "text": what})
    for width in (60, 78, 100, 118, 160):
        out = look(m, width=width, height=40)
        assert all(len(line) <= width for line in out.splitlines()), width


def test_long_conversation_text_wraps_under_its_label_instead_of_running_off():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "transcript", "role": "assistant", "text": "word " * 60})
    lines = [ln for ln in look(m, width=100, height=40).splitlines() if "word" in ln]
    assert len(lines) >= 3


# -- the album cover ----------------------------------------------------------------------------------------------------

def test_the_music_card_is_tall_with_the_cover_on_top_and_the_title_under_it():
    m = TuiModel(status=lambda: FULL_STATUS)
    out = look(m, width=110, height=40).splitlines()
    head = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    title = next(i for i, line in enumerate(out) if "Veridis Quo" in line)
    col = out[head].index("♪")
    cover_rows = [i for i in range(head + 1, title) if "▀" in out[i][col:col + 18]]
    assert title - head - 1 == 10 and len(cover_rows) == 9  # nine cover rows and a row of air between heading and title
    assert all(len(out[i]) <= 110 for i in range(len(out)))
    below = "\n".join(out[title:title + 5])
    assert "Daft Punk" in below and "1:23" in below.replace("1:00", "1:23") or "1:00" in below  # artist, progress under it
    card_width = out[head].index("│", col) - col + 3 if "│" in out[head][col:] else 0
    assert card_width <= 24  # narrow: it is a column, not a banner


def test_the_real_picture_replaces_the_tile_once_loaded():
    import asyncio

    from minty_core.audio.cover import CoverCache

    async def go():
        async def fetch(url):
            return b"IMG"

        m = TuiModel(status=lambda: {**FULL_STATUS, "now_playing": {**FULL_STATUS["now_playing"], "art_url": "https://x/a.jpg"}})
        m.covers = CoverCache(cols=18, rows=9, fetch=fetch, decode=lambda data, c, r: bytes([1, 2, 3] * (c * r * 2)))
        render(m, 110, 44, now=1.0, wall=WALL)  # tall enough for the full 18 x 9 cover
        await asyncio.sleep(0.1)
        art = m.covers.get({"art_url": "https://x/a.jpg", "title": "t", "artist": "a"}, 18, 9)
        return {str(s.style) for s in art.spans}

    assert asyncio.run(go()) == {"rgb(1,2,3) on rgb(1,2,3)"}


def test_the_card_stays_inside_its_box_with_a_cover():
    m = TuiModel(status=lambda: FULL_STATUS)
    for width in (96, 110, 150):
        out = look(m, width=width, height=36)
        assert all(len(line) <= width for line in out.splitlines()), width


# -- full-resolution cover on terminals that can show pictures -----------------------------------------------------------

from minty_core.gfx import Graphics, Picture  # noqa: E402

BIG = Picture(key="k", width=162, height=162, rgb=b"", png=b"PNG")


class FixedCovers:
    """A cover cache that already has the picture."""

    def __init__(self, picture):
        self.picture_, self.asked = picture, []

    def picture(self, track, w, h, protocol):
        self.asked.append((w, h, protocol))
        return self.picture_

    def get(self, track, cols=None, rows=None):
        from minty_core.audio.cover import tile
        return tile("x", cols or 18, rows or 9)

    def accent(self, track):
        return None  # the default colour



def with_graphics(picture=BIG, protocol="sixel", cell=(9, 18), **status):
    m = TuiModel(status=lambda: {**FULL_STATUS, **status})
    m.gfx = Graphics(protocol, *cell)
    m.covers = FixedCovers(picture)
    return m


def test_the_picture_is_placed_exactly_over_the_cells_the_card_kept_for_it():
    m = with_graphics()
    out = look(m, width=110, height=40).splitlines()
    pic, x, y, cols, rows = m.cover_at
    assert pic is BIG and (cols, rows) == (18, 9)
    header = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    assert y == header + 1 and x == out[header].index("♪")  # the cover starts under the card's heading, in its first column
    for line in out[y:y + rows]:  # the reserved block is blank text: the picture shows through it
        assert line[x:x + cols] == " " * cols
    assert "Veridis Quo" in out[y + rows + 1]  # a row of air, then the title, under it
    assert m.covers.asked[-1] == (162, 162, "sixel")  # 18 x 9 cells of 9 x 18 pixels, square


def test_the_square_is_the_smaller_side_of_the_block():
    m = with_graphics(cell=(8, 17))
    look(m, width=110, height=40)
    assert m.covers.asked[-1][:2] == (144, 144)  # 18 * 8 = 144 across, 9 * 17 = 153 down


def test_while_the_picture_loads_a_tile_fills_the_same_block_and_nothing_is_overlaid():
    m = with_graphics(picture=None)
    out = look(m, width=110, height=40)
    assert m.cover_at is None and "♪" in out and out.count("▀") > 18 * 8  # the 18 x 9 tile


def test_no_overlay_without_a_protocol_a_card_or_room():
    m = TuiModel(status=lambda: FULL_STATUS)
    look(m)
    assert m.cover_at is None  # half-block covers are plain text
    m = with_graphics()
    look(m, width=60, height=40)
    assert m.cover_at is None  # too narrow for the card
    m = with_graphics()
    m.apply({"type": "display", "title": "Todo", "content": "- milk"})
    look(m, width=110, height=40)
    assert m.cover_at is None  # the display pane moves the conversation: its position is not known
    m = with_graphics(now_playing=None)
    look(m, width=110, height=40)
    assert m.cover_at is None  # nothing playing
    m = with_graphics()
    look(m, width=110, height=14)
    assert m.cover_at is None  # the block would run off the bottom


def test_the_wider_card_still_fits_every_width():
    for width in (100, 110, 150):
        m = with_graphics()
        out = look(m, width=width, height=40)
        assert all(len(line) <= width for line in out.splitlines()), width


# -- wiring the pictures into the running display ------------------------------------------------------------------------

def test_the_terminal_is_asked_only_when_pictures_are_on_auto():
    from minty_core.tui import choose_graphics
    asked = []

    def fake_detect():
        asked.append(1)
        return "sixel"

    cell = lambda fd: (10, 20)  # noqa: E731
    assert choose_graphics("auto", 1, detect_fn=fake_detect, cell_fn=cell) == Graphics("sixel", 10, 20) and asked == [1]
    assert choose_graphics("off", 1, detect_fn=fake_detect, cell_fn=cell) is None and asked == [1]
    assert choose_graphics("kitty", 1, detect_fn=fake_detect, cell_fn=cell) == Graphics("kitty", 10, 20) and asked == [1]
    assert choose_graphics("auto", 1, detect_fn=lambda: None, cell_fn=cell) is None


def test_each_frame_draws_the_cover_where_the_layout_put_it_and_removes_it_when_it_goes():
    from minty_core.tui import paint_images
    calls = []

    class Overlay:
        def show(self, *args):
            calls.append(args)

    m = with_graphics()
    look(m, width=110, height=40)
    paint_images(m, Overlay())
    pic, x, y, cols, rows = m.cover_at
    assert calls[-1] == (pic, x, y, cols, rows)
    m.cover_at = None
    paint_images(m, Overlay())
    assert calls[-1][0] is None  # no cover this frame: the overlay removes the old one


# -- the cover size is a setting: about 256 pixels when asked for ---------------------------------------------------------

def test_a_bigger_cover_is_a_square_of_about_256_pixels_and_a_short_screen_shrinks_it():
    m = with_graphics()
    m.cover_cols = 28
    out = look(m, width=130, height=48).splitlines()
    pic, x, y, cols, rows = m.cover_at
    assert (cols, rows) == (28, 14) and m.covers.asked[-1] == (252, 252, "sixel")  # 28 * 9 = 252, 14 * 18 = 252
    header = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    assert y == header + 1 and x == out[header].index("♪")
    assert all(len(line) <= 130 for line in out)
    short = with_graphics()
    short.cover_cols = 28
    look(short, width=130, height=36)  # not enough rows for 14: the cover shrinks, still square
    cols, rows = short.cover_at[3:]
    assert cols == rows * 2 and 4 <= rows < 14


def test_the_default_cover_stays_18_cells():
    m = with_graphics()
    assert m.cover_cols == 18
    look(m, width=110, height=40)
    assert m.cover_at[3:] == (18, 9)


# -- the failsafe: any trouble with pictures drops back to the pixel-block cover -----------------------------------------------

def test_a_failure_while_drawing_switches_pictures_off_and_the_blocks_take_over():
    from minty_core.tui import safe_paint_images
    removed = []

    class Broken:
        def show(self, *args):
            raise OSError("the terminal went away")

        def clear(self):
            removed.append(1)

    m = with_graphics()
    look(m, width=110, height=40)
    assert m.cover_at is not None and m.gfx is not None
    assert safe_paint_images(m, Broken()) is False  # the display carries on
    assert m.gfx is None and m.cover_at is None and removed == [1]
    out = look(m, width=110, height=40)  # the next frame is the half-block cover again, and nothing is overlaid
    assert m.cover_at is None and "NOW PLAYING" in out and out.count("▀") > 12 * 5


def test_working_pictures_are_left_alone():
    from minty_core.tui import safe_paint_images
    shown = []

    class Fine:
        def show(self, *args):
            shown.append(args)

        def clear(self):
            raise AssertionError("must not be cleared")

    m = with_graphics()
    look(m, width=110, height=40)
    assert safe_paint_images(m, Fine()) is True and m.gfx is not None and len(shown) == 1


# -- the colours follow the album cover --------------------------------------------------------------------------------

import re as _re  # noqa: E402


class AccentCovers(FixedCovers):
    def __init__(self, accent):
        super().__init__(None)
        self.accent_ = accent

    def accent(self, track):
        return self.accent_


def ansi_of(model, width=110, height=36):
    """The picture of the screen as SVG: it carries every colour as a hex value (Rich caches ANSI codes per style, so the colour
    system an earlier test used would otherwise change what a later one sees)."""
    console = Console(file=io.StringIO(), width=width, height=height, force_terminal=True, color_system="truecolor",
                      record=True)
    console.print(render(model, width, height, now=1.0, wall=WALL))
    return console.export_svg().lower()


def test_the_headings_and_the_card_take_the_colour_of_the_cover():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.covers = AccentCovers((30, 160, 220))
    out = ansi_of(m)
    assert "#1ea0dc" in out  # the accent shows up as a truecolor foreground
    assert "#ff7a2f" not in out  # the default ember is gone while a cover sets the colour


def test_without_a_track_the_default_ember_stays_and_a_grey_cover_keeps_it():
    nothing = {**FULL_STATUS, "music": False, "now_playing": None}
    m = TuiModel(status=lambda: nothing)
    m.covers = AccentCovers((30, 160, 220))
    assert "#ff7a2f" in ansi_of(m) and "#1ea0dc" not in ansi_of(m)
    grey = TuiModel(status=lambda: FULL_STATUS)
    grey.covers = AccentCovers(None)
    assert "#ff7a2f" in ansi_of(grey)


def test_the_colour_fades_to_a_new_song_instead_of_jumping():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.covers = AccentCovers((255, 0, 0))
    ansi_of(m)
    first = m.accent_rgb
    m.covers = AccentCovers((0, 0, 255))
    ansi_of(m)
    mid = m.accent_rgb
    assert mid != first and mid != (0.0, 0.0, 255.0) and mid[2] > first[2] and mid[0] < first[0]  # on its way
    for _ in range(60):
        ansi_of(m)
    assert all(abs(a - b) < 3 for a, b in zip(m.accent_rgb, (0, 0, 255)))  # and it arrives


def test_reduced_motion_changes_the_colour_at_once():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.reduced_motion = True
    m.covers = AccentCovers((10, 200, 90))
    ansi_of(m)
    assert tuple(round(c) for c in m.accent_rgb) == (10, 200, 90)


def test_the_red_alpha_never_changes_colour(monkeypatch):
    monkeypatch.setattr("minty_core.tui.__version__", "1.0.1-alpha.1")  # a plain release has no stage to colour
    m = TuiModel(status=lambda: FULL_STATUS)
    m.covers = AccentCovers((30, 160, 220))
    assert "#e5484d" in ansi_of(m)  # the stage keeps its red (#e5484d) whatever the song's colour is


# -- the card's play icon and equaliser ---------------------------------------------------------------------------------------

def test_the_play_icon_is_three_rows_the_equaliser_moves_and_the_player_name_is_gone():
    from minty_core.tui import PLAY_ICON, equaliser
    m = TuiModel(status=lambda: FULL_STATUS)
    out = look(m, width=110, height=44)
    assert all(row.strip() in out for row in PLAY_ICON)  # the big triangle, all three rows
    assert "spotify" not in out.lower()
    a, b = equaliser(1.0, 6, False), equaliser(1.4, 6, False)
    assert len(a) == 3 and {len(r) for r in a} == {12} and a != b  # three rows of six two-cell bars, and they move
    still = equaliser(1.0, 6, True)
    assert still == equaliser(9.0, 6, True)  # reduced motion: flat and steady


def test_paused_shows_a_big_pause_icon_and_the_word():
    from minty_core.tui import PAUSE_ICON
    paused = {**FULL_STATUS, "now_playing": {**FULL_STATUS["now_playing"], "status": "Paused"}}
    out = look(TuiModel(status=lambda: paused), width=110, height=44)
    assert all(row in out for row in PAUSE_ICON) and "paused" in out


# ============================ round 2: a quieter screen, a chat bar, more of the cover's colour ============================

def test_the_status_is_one_quiet_line_that_only_speaks_up_when_something_needs_attention():
    from minty_core.tui import status_strip
    calm = status_strip(FULL_STATUS).plain
    assert "claude" in calm and "classic" in calm.lower() and "mic on" in calm.lower()
    for noise in ("THINKING", "CAPACITY", "MEMORY", "kokoro", "learned", "9/9", "BRAIN", "TODAY", "switch to"):
        assert noise not in calm, noise
    loud = status_strip({**FULL_STATUS, "muted": True, "private": True, "tier": "deep", "brain_ready": 7, "brain_total": 9,
                         "capacity": {"share": 0.1, "calls_left": 8, "forecast": "runs out around 3:10 PM"},
                         "work": [{"id": 1, "kind": "coding", "what": "x", "secs": 3, "status": "running"}],
                         "privacy_banner": "on"}).plain
    for expected in ("MUTED", "private", "deep", "7/9", "10%", "1 coding job", "privacy"):
        assert expected.lower() in loud.lower(), expected
    assert status_strip({}).plain == ""


def test_the_screen_has_no_status_panel_and_no_switch_list():
    out = look(TuiModel(status=lambda: FULL_STATUS), width=120, height=40)
    for gone in ("Status", "THINKING", "CAPACITY", "MEMORY", "say “switch to"):
        assert gone not in out, gone
    assert "claude" in out and "classic" in out.lower()


def test_typing_fills_the_chat_bar_and_enter_sends_it():
    from minty_core.tui import handle_key
    m = TuiModel()
    for ch in "what time is it":
        assert handle_key(m, ch) is None
    assert m.buffer == "what time is it"
    handle_key(m, "\x7f")
    assert m.buffer == "what time is i"
    handle_key(m, "\x15")  # ctrl-u clears the line
    assert m.buffer == ""
    for ch in "hello":
        handle_key(m, ch)
    assert handle_key(m, "\r") == "hello" and m.buffer == ""
    assert handle_key(m, "\r") is None  # an empty line sends nothing
    assert handle_key(m, "\x1b") is None and handle_key(m, "\x03") is None and m.buffer == ""  # control keys are not text
    for ch in "x" * 600:
        handle_key(m, ch)
    assert len(m.buffer) == 500  # a sane limit


def test_the_chat_bar_shows_a_prompt_what_you_type_the_words_as_they_are_heard_and_what_minty_says():
    m = TuiModel(status=lambda: FULL_STATUS)
    idle = look(m, width=120, height=40)
    assert "Say “Minty”" in idle and "type" in idle.lower()
    m.buffer = "buy milk"
    assert "buy milk" in look(m, width=120, height=40)
    m.buffer = ""
    m.apply({"type": "state", "state": "listening"})
    m.apply({"type": "partial", "text": "what is the weather", "tentative": " like"})
    out = look(m, width=120, height=40)
    assert "what is the weather" in out.splitlines()[-2] or "what is the weather" in "\n".join(out.splitlines()[-4:])  # in the bar


def test_minty_words_appear_one_by_one_as_they_are_spoken_and_then_stay_for_a_while():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "state", "state": "speaking"})
    m.apply({"type": "transcript", "role": "assistant", "text": "It is sunny and warm in Paris today."})
    start = m.said_at
    first = m.spoken_words(start + 0.1)
    later = m.spoken_words(start + 1.2)
    done = m.spoken_words(start + 5.0)
    assert 1 <= len(first.split()) < len(later.split()) < len(done.split()) == 8 and done.startswith(first.split()[0])
    assert m.spoken_words(start + 30.0) == ""  # it fades after a while
    m.apply({"type": "transcript", "role": "user", "text": "thanks"})
    assert m.spoken_words(start + 1.0) == ""  # the next thing you say replaces it


def test_the_bar_sits_at_the_bottom_and_everything_still_fits():
    m = TuiModel(status=lambda: FULL_STATUS)
    for width, height in ((60, 24), (80, 30), (100, 36), (120, 40), (160, 50)):
        out = look(m, width=width, height=height).splitlines()
        assert all(len(line) <= width for line in out), (width, height)
        assert "type" in "\n".join(out[-4:]).lower(), (width, height)  # the placeholder is in the last rows


def test_the_card_has_a_row_of_air_under_the_cover_and_the_title_hugs_its_artist():
    m = TuiModel(status=lambda: FULL_STATUS)
    out = look(m, width=120, height=44).splitlines()
    title = next(i for i, line in enumerate(out) if "Veridis Quo" in line)
    artist = next(i for i, line in enumerate(out) if "Daft Punk" in line)
    assert artist == title + 1  # no empty row between the name and the artist any more
    head = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    assert title - head == 9 + 1 + 1  # heading, nine cover rows, one row of air, then the title


def test_a_two_line_title_still_works():
    st = {**FULL_STATUS, "now_playing": {**FULL_STATUS["now_playing"], "title": "A Very Long Song Title That Wraps Round"}}
    out = look(TuiModel(status=lambda: st), width=120, height=44)
    assert "A Very Long Song" in out and "Daft Punk" in out


def test_the_idle_orb_and_header_take_the_colour_of_the_song_but_active_states_keep_their_own():
    from minty_core.tui import orb

    def colours(model):
        return {str(s.style) for s in orb(model, 1.0).spans}

    a, b = TuiModel(), TuiModel()
    a.accent_rgb, b.accent_rgb = (255.0, 60.0, 60.0), (60.0, 120.0, 255.0)
    a.state = b.state = "idle"
    assert colours(a) != colours(b)  # a red song and a blue song give different idle orbs
    a.state = b.state = "listening"
    assert colours(a) == colours(b)  # listening is always the same cyan
    m = TuiModel(status=lambda: FULL_STATUS)
    m.state, m.reduced_motion = "idle", True
    m.covers = AccentCovers((30, 160, 220))
    svg = ansi_of(m)
    from minty_core.tui import _shade
    assert _shade((30.0, 160.0, 220.0), 0.45) in svg  # the header border: a dim tone of the song's colour


def test_escape_sequences_from_the_arrow_keys_are_dropped_and_text_survives():
    from minty_core.tui import parse_keys
    assert parse_keys("hi\x1b[A\x1b[B there\r") == list("hi there") + ["\r"]
    assert parse_keys("\x1bOP\x1b[1;5C ok") == [" ", "o", "k"]
    assert parse_keys("café") == list("café") and parse_keys("") == []


def test_when_what_you_said_is_final_the_live_words_leave_the_bar():
    m = TuiModel(status=lambda: FULL_STATUS)
    m.apply({"type": "state", "state": "listening"})
    m.apply({"type": "partial", "text": "turn on the", "tentative": " light"})
    assert "turn on the light" in look(m, width=120, height=40)
    m.apply({"type": "transcript", "role": "user", "text": "turn on the light"})
    m.apply({"type": "state", "state": "thinking"})
    out = look(m, width=120, height=40).splitlines()
    assert "you ›" not in "\n".join(out[-4:])


# ===================== round 3: the card at the bottom right, a "coming up" column, a narrower chat =====================

AGENDA = [{"kind": "timer", "text": "tea", "due": 0.0, "remaining_s": 272.0},
          {"kind": "reminder", "text": "call dad", "due": datetime(2026, 10, 19, 23, 59).timestamp(), "remaining_s": 4000.0},
          {"kind": "reminder", "text": "dentist appointment", "due": datetime(2026, 10, 21, 9, 30).timestamp(), "remaining_s": 90000.0}]


def test_the_columns_split_the_conversation_panel_roughly_in_half_for_the_chat():
    from minty_core.tui import column_widths
    chat, middle, card = column_widths(120, card_w=22)
    assert chat == int((120 - 4) * 0.5) and card == 24 and chat + middle + card == 120 - 4 and middle >= 24
    chat, middle, card = column_widths(90, card_w=22)
    assert middle == 0 and chat + card == 90 - 4  # too narrow for the middle: the chat takes it
    assert column_widths(120, card_w=0)[2] == 0  # no card: no column for it


def test_the_free_space_lists_what_is_coming_up():
    st = {**FULL_STATUS, "agenda": AGENDA}
    out = look(TuiModel(status=lambda: st), width=124, height=40)
    for expected in ("COMING UP", "tea", "4:32", "call dad", "11:59 PM", "dentist appointment", "Wed 9:30 AM"):
        assert expected in out, expected
    nothing = look(TuiModel(status=lambda: {**FULL_STATUS, "agenda": []}), width=124, height=40)
    assert "COMING UP" in nothing and "Nothing coming up" in nothing


def test_the_card_sits_at_the_bottom_right_with_a_little_padding():
    m = TuiModel(status=lambda: FULL_STATUS)
    out = look(m, width=124, height=40).splitlines()
    chat_top = len(out) - 3  # the chat bar is the last three rows
    panel_bottom = max(i for i in range(chat_top) if out[i].startswith("╰"))
    card_bottom = max(i for i in range(panel_bottom) if "╰" in out[i][60:] and i != panel_bottom)
    assert card_bottom == panel_bottom - 2  # one empty row of padding under the card
    head = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    col = out[head].index("♪")
    assert col + 22 <= 124 - 2 - 1 + 2  # one column of padding to the right edge of the panel
    assert out[head][col + 21:col + 24].strip() in ("│", "")  # the card's right border is still inside the panel


def test_the_cover_overlay_follows_the_card_to_the_bottom():
    m = with_graphics()
    out = look(m, width=124, height=44).splitlines()
    pic, x, y, cols, rows = m.cover_at
    head = next(i for i, line in enumerate(out) if "NOW PLAYING" in line)
    assert y == head + 1 and x == out[head].index("♪")
    for line in out[y:y + rows]:
        assert line[x:x + cols] == " " * cols


def test_the_chat_keeps_to_its_half_and_long_replies_wrap_inside_it():
    m = TuiModel(status=lambda: {**FULL_STATUS, "agenda": AGENDA})
    m.apply({"type": "transcript", "role": "assistant", "text": "word " * 80})
    out = look(m, width=124, height=40).splitlines()
    rows = [ln for ln in out if "word" in ln]
    assert len(rows) >= 4 and all(ln.rstrip().rindex("word") < int(124 * 0.56) for ln in rows)


def test_a_narrow_screen_drops_the_middle_column_and_keeps_everything_inside_the_width():
    st = {**FULL_STATUS, "agenda": AGENDA}
    for width in (70, 90, 100, 110, 124, 160):
        out = look(TuiModel(status=lambda: st), width=width, height=40).splitlines()
        assert all(len(line) <= width for line in out), width
    assert "COMING UP" not in look(TuiModel(status=lambda: st), width=90, height=40)


# -- the display adapts its own speed to the machine: same look, fewer frames when frames are expensive ------------------------

def test_a_cheap_frame_changes_nothing_and_an_expensive_one_slows_the_redraws_not_the_look():
    from minty_core.tui import ACTIVE_FPS, IDLE_FPS, frame_delay
    m = TuiModel()
    m.state = "idle"
    assert frame_delay(m, render_s=0.006) == 1 / IDLE_FPS  # this PC: exactly as before
    assert frame_delay(m, render_s=0.060) > 1.0  # ten times slower: about one frame a second while waiting
    assert frame_delay(m, render_s=5.0) == 2.0  # but never slower than one frame in two seconds
    m.state = "listening"
    assert frame_delay(m, render_s=0.006) == 1 / ACTIVE_FPS
    assert 0.15 < frame_delay(m, render_s=0.060) < 0.2  # smooth enough while something is happening: a third of a core at most
    assert frame_delay(m, render_s=5.0) == 0.4
    assert frame_delay(m, False, 0.0) == 1 / ACTIVE_FPS  # no measurement yet: the normal rate
