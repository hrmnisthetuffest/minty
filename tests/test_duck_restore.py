"""Turning other apps down while Minty listens must never leave one of them quiet.

The sound server remembers the volume last set on a stream *per application* (WirePlumber's `stream-properties`), so a stream
that was turned down and then closed (a browser does that whenever a video stops) leaves the next stream of that app starting
quiet. The fake below behaves like that."""
import time

from minty_core.voice.adapt import Ducker

OWN = 4242


class Pulse:
    """pactl as the sound server answers it: a new stream starts at the volume last set on its app, a stream that is gone cannot be set."""

    def __init__(self):
        self.streams, self.remembered, self.calls, self.n = {}, {}, [], 100

    def spawn(self, app, pid="900", ident=None):
        if ident is None:
            ident, self.n = str(self.n), self.n + 1
        self.streams[ident] = {"app": app, "pid": pid, "vol": self.remembered.get(app, 100)}
        return ident

    def kill(self, ident):
        self.streams.pop(ident)

    def vol(self, ident):
        return self.streams[ident]["vol"]

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["pactl", "list", "sink-inputs"]:
            return 0, "".join(
                f'Sink Input #{i}\n\tVolume: front-left: 65536 / {s["vol"]}% / 0.00 dB\n\tProperties:\n'
                f'\t\tapplication.name = "{s["app"]}"\n\t\tapplication.process.id = "{s["pid"]}"\n' for i, s in self.streams.items())
        if argv[:2] == ["pactl", "set-sink-input-volume"]:
            s = self.streams.get(argv[2])
            if s is None:
                return 1, "Failure: No such entity"
            s["vol"] = int(argv[3].rstrip("%"))
            self.remembered[s["app"]] = s["vol"]
            return 0, ""
        return 0, ""


def ducker(pa, **kw):
    kw.setdefault("poll_s", 0)
    return Ducker(pa, own_pid=OWN, level=0.1, **kw)


def test_a_stream_that_replaced_the_turned_down_one_is_turned_back_up():
    pa = Pulse()
    old = pa.spawn("Zen")
    d = ducker(pa)
    assert d.begin() and pa.vol(old) == 10
    pa.kill(old)  # the browser closed that stream and opened another, which starts where the server remembers: quiet
    new = pa.spawn("Zen")
    assert pa.vol(new) == 10
    d.restore()
    assert pa.vol(new) == 100


def test_with_no_stream_left_the_first_new_one_is_turned_up_as_soon_as_it_is_seen():
    pa = Pulse()
    old = pa.spawn("Zen")
    d = ducker(pa)
    d.begin()
    pa.kill(old)
    d.restore()
    assert pa.remembered["Zen"] == 10  # the trap: nothing could be set, the server still remembers 10
    new = pa.spawn("Zen")
    assert pa.vol(new) == 10
    assert d.repair() == 0 and pa.vol(new) == 100


def test_a_quiet_stream_is_not_taken_for_the_normal_volume_next_time():
    pa = Pulse()
    old = pa.spawn("Zen")
    d = ducker(pa)
    d.begin()
    pa.kill(old)
    d.restore()
    new = pa.spawn("Zen")  # starts at 10
    d.begin()
    d.restore()
    assert pa.vol(new) == 100  # not 10: it did not ratchet down


def test_the_debt_survives_a_restart(tmp_path):
    pa, state = Pulse(), tmp_path / "ducked.json"
    old = pa.spawn("Zen")
    d = ducker(pa, state_path=state)
    d.begin()
    pa.kill(old)
    d.restore()
    assert state.exists()  # what is still owed is written down
    new = pa.spawn("Zen")
    ducker(pa, state_path=state).recover()
    assert pa.vol(new) == 100


def test_a_killed_run_note_never_touches_another_app_that_took_the_number(tmp_path):
    pa, state = Pulse(), tmp_path / "ducked.json"
    zen = pa.spawn("Zen")
    ducker(pa, state_path=state).begin()  # killed here: no restore
    pa.kill(zen)
    music = pa.spawn("Spotify", ident=zen)  # stream numbers are used again
    pa.streams[music]["vol"] = 40
    ducker(pa, state_path=state).recover()
    assert pa.vol(music) == 40
    new = pa.spawn("Zen")
    assert pa.vol(new) == 10
    d = ducker(pa, state_path=state)
    d.recover()
    assert d.repair() == 0 and pa.vol(new) == 100


def test_a_note_from_before_streams_were_named_is_dropped_not_guessed_at(tmp_path):
    pa, state = Pulse(), tmp_path / "ducked.json"
    other = pa.spawn("Spotify")
    pa.streams[other]["vol"] = 40
    state.write_text('{"%s": 100}' % other)
    ducker(pa, state_path=state).recover()
    assert pa.vol(other) == 40 and not state.exists()


def test_a_volume_you_set_yourself_is_left_alone():
    pa = Pulse()
    old = pa.spawn("Zen")
    d = ducker(pa)
    d.begin()
    pa.kill(old)
    d.restore()
    pa.remembered["Zen"] = 60  # you turned the browser to 60 yourself
    new = pa.spawn("Zen")
    assert d.repair() == 0 and pa.vol(new) == 60


def test_a_debt_older_than_two_days_is_forgotten():
    pa, now = Pulse(), [1_000_000.0]
    old = pa.spawn("Zen")
    d = ducker(pa, clock=lambda: now[0])
    d.begin()
    pa.kill(old)
    d.restore()
    now[0] += 3 * 86400
    new = pa.spawn("Zen")
    assert d.repair() == 0 and pa.vol(new) == 10


def test_every_stream_of_the_app_comes_back_and_the_apps_own_stream_is_never_touched():
    pa = Pulse()
    a, b = pa.spawn("Zen"), pa.spawn("Zen")
    mine = pa.spawn("minty", pid=str(OWN))
    d = ducker(pa)
    d.begin()
    assert pa.vol(a) == pa.vol(b) == 10 and pa.vol(mine) == 100
    d.restore()
    assert pa.vol(a) == pa.vol(b) == 100 and pa.vol(mine) == 100
    assert not any(c[:3] == ["pactl", "set-sink-input-volume", mine] for c in pa.calls)


def test_a_normal_round_trip_asks_for_nothing_extra():
    pa = Pulse()
    pa.spawn("Zen")
    d = ducker(pa)
    d.begin()
    d.restore()
    assert [c[2:] for c in pa.calls if c[:2] == ["pactl", "set-sink-input-volume"]] == [["100", "10%"], ["100", "100%"]]


def test_the_watcher_pays_the_debt_by_itself():
    pa = Pulse()
    old = pa.spawn("Zen")
    d = Ducker(pa, own_pid=OWN, level=0.1, poll_s=0.01)
    d.begin()
    pa.kill(old)
    d.restore()
    new = pa.spawn("Zen")
    deadline = time.time() + 3
    while pa.vol(new) != 100 and time.time() < deadline:
        time.sleep(0.01)
    assert pa.vol(new) == 100
