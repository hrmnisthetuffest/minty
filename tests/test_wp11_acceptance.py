"""WP-11 acceptance: HUD visibility within a second, timers across a restart, and the cost of a tick."""
import time

import pytest

from tests.plugin_helpers import make_host
from minty_core.overlay.model import OverlayModel
from minty_core.overlay.xwin import ActiveWindow, ActiveWindowReader
from minty_core.timerstore import TimerStore

POLL_S = 0.5  # the HUD polls the active window every 500 ms


class Desk:
    """A scripted desktop: which window is active at each moment."""

    def __init__(self):
        self.now, self.active = 0.0, ActiveWindow("0x1", "minty-core run", "gnome-terminal-server", 1)

    def read(self):
        return self.active


def test_hidden_with_the_terminal_focused_and_shown_within_a_second_of_switching_apps():
    desk, model = Desk(), OverlayModel()
    assert model.frame(desk.read()).visible is False  # terminal focused
    switch_at, shown_at = 3.2, None
    t = 0.0
    while t < 6 and shown_at is None:
        t += POLL_S
        if t >= switch_at:
            desk.active = ActiveWindow("0x2", "Docs - Google Chrome", "google-chrome", 7)
        if model.frame(desk.read()).visible:
            shown_at = t
    assert shown_at is not None and shown_at - switch_at <= 1.0


def test_it_goes_away_again_when_you_return_to_the_terminal():
    desk, model = Desk(), OverlayModel()
    desk.active = ActiveWindow("0x2", "Firefox", "firefox", 7)
    assert model.frame(desk.read()).visible
    desk.active = ActiveWindow("0x1", "minty-core run", "gnome-terminal-server", 1)
    assert model.frame(desk.read()).visible is False


async def test_a_timer_survives_a_restart_and_shows_the_right_time_left(tmp_path):
    host = make_host(tmp_path)
    await host.call("timers_set_timer", {"seconds": 600, "label": "tea"})
    later = TimerStore(tmp_path / "timers.json", clock=lambda: time.time() + 100)  # a restart 100 s on
    (timer,) = later.all()
    assert timer.label == "tea" and 495 <= timer.remaining(later.clock()) <= 505


def test_a_tick_is_cheap_enough_for_under_a_percent_of_one_core():
    model = OverlayModel()
    model.apply({"state": "listening", "partial": "what is", "tentative": " the", "sentence": "", "sentence_age_s": 99,
                 "notice": "", "notice_age_s": 99,
                 "timers": [{"label": "tea", "kind": "timer", "remaining_s": 100}] * 3})
    w = ActiveWindow("0x2", "Firefox", "firefox", 7)
    start = time.perf_counter()
    for _ in range(2000):
        model.frame(w)
    per_tick = (time.perf_counter() - start) / 2000
    assert per_tick < 0.001  # two ticks a second at under a millisecond each: well below 1 % of a core


def test_the_real_active_window_can_be_read_on_this_machine_or_it_says_it_cannot():
    reader = ActiveWindowReader()
    if not reader.available():
        pytest.skip("no X11 session with xprop here")
    w = reader.read()
    assert w is None or (w.id.startswith("0x") and isinstance(w.title, str))
