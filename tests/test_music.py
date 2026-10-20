import numpy as np
import pytest

from minty_core.audio.music import MusicWatcher
from minty_core.audio.wakewords import TranscriptWakeDetector, match_wake
from minty_core.config import AudioConfig

PHRASES = ["minty", "mintee", "minti", "mindy"]


def runner(players=None, sinks=""):
    calls = []

    def run(args):
        calls.append(args)
        if args[:2] == ["playerctl", "-l"]:
            return "\n".join(players or {})
        if args[:2] == ["playerctl", "-p"] and args[3] == "status":
            return (players or {}).get(args[2], "") + "\n"
        if args[:3] == ["pactl", "list", "short"]:
            return sinks
        return ""

    run.calls = calls
    return run


def test_player_status_means_playing_and_result_is_cached():
    clock = [0.0]
    run = runner({"spotify": "Playing"})
    w = MusicWatcher(run, ttl=2.0, clock=lambda: clock[0])
    assert w.is_playing() is True
    n = len(run.calls)
    assert w.is_playing() is True and len(run.calls) == n  # cached
    clock[0] = 5.0
    w.is_playing()
    assert len(run.calls) > n


def test_paused_player_and_idle_sink_are_not_music():
    assert MusicWatcher(runner({"spotify": "Paused"}, "1\talsa\tPipeWire\ts32le\tSUSPENDED")).is_playing() is False


def test_running_sink_counts_when_no_player_exposes_mpris():
    assert MusicWatcher(runner({}, "1\talsa\tPipeWire\ts32le 2ch\tRUNNING")).is_playing() is True


def test_pause_and_resume_only_touch_what_was_playing():
    run = runner({"spotify": "Playing", "firefox": "Paused"})
    w = MusicWatcher(run)
    w.pause()
    assert w.paused == ["spotify"]
    w.resume()
    cmds = [c for c in run.calls if c[-1] in ("pause", "play")]
    assert cmds == [["playerctl", "-p", "spotify", "pause"], ["playerctl", "-p", "spotify", "play"]]


@pytest.mark.parametrize("text, expected", [
    ("Minty what time is it", "what time is it"),
    ("hey minty", ""),
    ("mintee what time is it", None),  # fuzzy/alias not accepted while music plays? exact alias is allowed
])
def test_strict_matching(text, expected):
    got = match_wake(text, PHRASES, strict=True)
    assert got == expected or (text.startswith("mintee") and got == "what time is it")


def test_strict_rejects_lyrics_and_near_misses():
    lyrics = "minty is the name of the song that we sing every night when the lights go down low"
    assert match_wake(lyrics, PHRASES, strict=True) is None
    assert match_wake("mintys stop", PHRASES, strict=True) is None
    assert match_wake("mintys stop", PHRASES, strict=False) == "stop"


def frame(level):
    return np.full(1280, level, dtype=np.int16).tobytes()


def speak(det, n_loud, n_quiet=12):
    return any([det.detect(frame(3000)) for _ in range(n_loud)] + [det.detect(frame(0)) for _ in range(n_quiet)])


def test_detector_ignores_long_bursts_while_music_plays():
    calls = []
    det = TranscriptWakeDetector(lambda pcm: calls.append(1) or "minty stop", AudioConfig, music=lambda: True)
    assert speak(det, 100) is False and calls == []  # ~8 s of sound
    det2 = TranscriptWakeDetector(lambda pcm: "minty stop", AudioConfig, music=lambda: True)
    assert speak(det2, 20) is True and det2.remainder == "stop"  # a short call is still heard


def test_music_does_not_change_matching_when_not_playing():
    det = TranscriptWakeDetector(lambda pcm: "mintys stop", AudioConfig, music=lambda: False)
    assert speak(det, 20) is True


def test_a_hung_player_is_asked_once_then_skipped_for_a_while():
    clock = [0.0]
    calls = []

    def run(args):
        calls.append(args)
        if args[:2] == ["playerctl", "-l"]:
            return "spotify\nvlc\n"
        if args[:3] == ["playerctl", "-p", "spotify"]:
            return None  # did not answer in time
        if args[:3] == ["playerctl", "-p", "vlc"]:
            return "Playing\n"
        return ""

    w = MusicWatcher(run, ttl=0.0, clock=lambda: clock[0])
    assert w.is_playing() is True  # vlc still counts
    asked = [c for c in calls if c[:3] == ["playerctl", "-p", "spotify"]]
    w.is_playing()
    w.pause()
    assert [c for c in calls if c[:3] == ["playerctl", "-p", "spotify"]] == asked  # not asked again
    assert w.paused == ["vlc"]
    clock[0] = 61.0
    w.is_playing()
    assert len([c for c in calls if c[:3] == ["playerctl", "-p", "spotify"]]) == len(asked) + 1  # retried later


def test_the_background_mode_never_makes_the_caller_wait():
    import threading
    import time as _time
    release = threading.Event()

    def slow(args):
        release.wait(2)
        return "spotify\n" if args[:2] == ["playerctl", "-l"] else "Playing\n"

    w = MusicWatcher(slow, ttl=2.0, background=True)
    started = _time.perf_counter()
    assert w.is_playing() is False and w.is_playing() is False  # last known answer, at once
    assert _time.perf_counter() - started < 0.05
    release.set()
    for _ in range(100):
        if w.is_playing():
            break
        _time.sleep(0.01)
    assert w._playing is True  # the refresh landed in the background


def track_runner(players, meta):
    """players: name -> status; meta: name -> 'artist|||title|||album|||length_us|||position_us'."""
    calls = []

    def run(args):
        calls.append(args)
        if args[:2] == ["playerctl", "-l"]:
            return "\n".join(players)
        if args[:2] == ["playerctl", "-p"] and args[3] == "status":
            return players[args[2]] + "\n"
        if args[:2] == ["playerctl", "-p"] and args[3] == "metadata":
            return meta[args[2]] + "\n"
        return ""

    run.calls = calls
    return run


def test_now_playing_reads_the_track_and_moves_the_position_while_it_plays():
    clock = [100.0]
    run = track_runner({"spotify": "Playing"}, {"spotify": "Daft Punk|||Veridis Quo|||Discovery|||345000000|||60000000"})
    w = MusicWatcher(run, ttl=2.0, clock=lambda: clock[0])
    t = w.now_playing()
    assert (t["title"], t["artist"], t["album"], t["status"], t["player"]) == (
        "Veridis Quo", "Daft Punk", "Discovery", "Playing", "spotify")
    assert (t["length_s"], round(t["position_s"])) == (345, 60)
    clock[0] = 110.0  # ten seconds later, between two refreshes: the bar keeps moving
    assert round(w.now_playing()["position_s"]) in (70, 60)  # 70 when the cache is reused, 60 at a fresh read


def test_a_paused_track_is_shown_still_and_a_playing_one_wins_over_it():
    run = track_runner({"a": "Paused", "b": "Playing"}, {"a": "X|||Old|||Al|||1000000|||500000",
                                                         "b": "Y|||New|||Al|||2000000|||1000000"})
    assert MusicWatcher(run, clock=lambda: 0.0).now_playing()["title"] == "New"
    run = track_runner({"a": "Paused"}, {"a": "X|||Old|||Al|||200000000|||5000000"})
    t = MusicWatcher(run, clock=lambda: 0.0).now_playing()
    assert t["status"] == "Paused" and t["title"] == "Old"


def test_no_player_or_a_stopped_one_means_no_track_and_a_missing_length_is_zero():
    assert MusicWatcher(track_runner({}, {}), clock=lambda: 0.0).now_playing() is None
    assert MusicWatcher(track_runner({"a": "Stopped"}, {"a": ""}), clock=lambda: 0.0).now_playing() is None
    run = track_runner({"a": "Playing"}, {"a": "|||A stream|||||||||"})
    t = MusicWatcher(run, clock=lambda: 0.0).now_playing()
    assert t["title"] == "A stream" and t["length_s"] == 0 and t["artist"] == ""


def test_a_player_that_hangs_is_skipped_for_the_track_too():
    def run(args):
        if args[:2] == ["playerctl", "-l"]:
            return "spotify"
        return None  # no answer in time

    w = MusicWatcher(run, clock=lambda: 0.0)
    assert w.now_playing() is None


def test_the_track_carries_the_address_of_its_cover():
    run = track_runner({"spotify": "Playing"}, {"spotify": "Daft Punk|||Veridis Quo|||Discovery|||345000000|||0|||https://i.scdn.co/image/abc"})
    assert MusicWatcher(run, clock=lambda: 0.0).now_playing()["art_url"] == "https://i.scdn.co/image/abc"
    run = track_runner({"spotify": "Playing"}, {"spotify": "A|||B|||C|||1000000|||0"})  # a player with no art field
    assert MusicWatcher(run, clock=lambda: 0.0).now_playing()["art_url"] == ""
