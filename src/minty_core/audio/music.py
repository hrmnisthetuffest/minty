"""Knows when music (or any media) is playing, and can pause/resume it around a conversation.

Media players answer over D-Bus (MPRIS), and a player can hang without answering (Spotify does). So:
- nobody ever waits for a player: `is_playing()` returns the last known answer at once and refreshes it in the
  background;
- each query has a short timeout, and a player that timed out is skipped for a while instead of costing the
  same wait on every check.
"""
import logging
import subprocess
import threading
import time

log = logging.getLogger(__name__)

QUERY_TIMEOUT_S = 0.6  # a healthy player answers in a few milliseconds
HUNG_RETRY_S = 60.0  # a player that did not answer is left alone this long
TRACK_FORMAT = "{{artist}}|||{{title}}|||{{album}}|||{{mpris:length}}|||{{position}}|||{{mpris:artUrl}}"  # microseconds for the two before the art address


def _run(args: list[str]) -> str | None:
    """stdout of a successful command, '' when it failed, None when it did not answer in time."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=QUERY_TIMEOUT_S, encoding="utf-8", errors="replace")
        return done.stdout if done.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        return None
    except (OSError, subprocess.SubprocessError):
        return ""


class MusicWatcher:
    def __init__(self, runner=_run, ttl: float = 2.0, clock=time.monotonic, background: bool | None = None):
        self._run, self._ttl, self._clock = runner, ttl, clock
        self._background = (runner is _run) if background is None else background
        self._at, self._playing = -1e9, False
        self._hung: dict[str, float] = {}  # player -> when to try it again
        self._refreshing = threading.Lock()
        self._track_lock = threading.Lock()
        self._track_at, self._track = -1e9, None
        self.paused: list[str] = []

    def is_playing(self) -> bool:
        """True while a media player (MPRIS) or the sound server reports audio playing. Never blocks in the
        background mode: the answer may be up to `ttl` seconds old."""
        now = self._clock()
        if now - self._at >= self._ttl:
            if not self._background:
                self.refresh()
            elif self._refreshing.acquire(blocking=False):
                self._at = now  # one refresh at a time; callers keep the previous answer meanwhile
                threading.Thread(target=self._refresh_then_release, name="music-watch", daemon=True).start()
        return self._playing

    def _refresh_then_release(self) -> None:
        try:
            self.refresh()
        finally:
            self._refreshing.release()

    def refresh(self) -> bool:
        self._at = self._clock()
        self._playing = bool(self._players("Playing")) or self._sink_running()
        return self._playing

    def now_playing(self) -> dict | None:
        """The track on the first playing player (or, failing that, a paused one): title, artist, album, status,
        position and length in seconds, player. Never blocks: the answer may be `ttl` seconds old, and the position of
        a playing track is carried forward so a progress bar keeps moving between refreshes."""
        now = self._clock()
        if now - self._track_at >= self._ttl:
            if not self._background:
                self._refresh_track()
            elif self._track_lock.acquire(blocking=False):
                self._track_at = now
                threading.Thread(target=self._refresh_track_then_release, name="music-track", daemon=True).start()
        t = self._track
        if t is None:
            return None
        out = {k: v for k, v in t.items() if k != "read_at"}
        if t["status"] == "Playing":
            moved = t["position_s"] + (now - t["read_at"])
            out["position_s"] = min(moved, t["length_s"]) if t["length_s"] else moved
        return out

    def _refresh_track_then_release(self) -> None:
        try:
            self._refresh_track()
        finally:
            self._track_lock.release()

    def _refresh_track(self) -> None:
        self._track_at = self._clock()
        now, paused, playing = self._clock(), None, None
        for name in self._ask(["playerctl", "-l"]).split():
            if self._hung.get(name, 0.0) > now:
                continue
            status = self._ask(["playerctl", "-p", name, "status"], name).strip()
            if status not in ("Playing", "Paused"):
                continue
            raw = self._ask(["playerctl", "-p", name, "metadata", "--format", TRACK_FORMAT], name).rstrip("\n")
            artist, title, album, length, position, art = (raw.split("|||") + [""] * 6)[:6]

            def seconds(value: str) -> float:
                try:
                    return max(0.0, float(value) / 1e6)
                except ValueError:
                    return 0.0

            track = {"title": title.strip(), "artist": artist.strip(), "album": album.strip(), "status": status,
                     "player": name, "art_url": art.strip(), "length_s": int(seconds(length)), "position_s": seconds(position), "read_at": now}
            if status == "Playing":
                playing = track
                break
            paused = paused or track
        self._track = playing or paused

    def _ask(self, args: list[str], player: str | None = None) -> str:
        out = self._run(args)
        if out is None:
            if player is not None:
                if player not in self._hung:
                    log.warning("media player %s is not answering; skipping it for %.0f s", player, HUNG_RETRY_S)
                self._hung[player] = self._clock() + HUNG_RETRY_S
            return ""
        return out

    def _players(self, wanted: str) -> list[str]:
        out = []
        now = self._clock()
        for name in self._ask(["playerctl", "-l"]).split():
            if self._hung.get(name, 0.0) > now:
                continue
            if self._ask(["playerctl", "-p", name, "status"], name).strip() == wanted:
                out.append(name)
        return out

    def _sink_running(self) -> bool:
        return any(line.split("\t")[-1:] == ["RUNNING"]
                   for line in self._ask(["pactl", "list", "short", "sinks"]).splitlines())

    def pause(self) -> None:
        self.paused = self._players("Playing")
        for name in self.paused:
            self._ask(["playerctl", "-p", name, "pause"], name)
        self._at = -1e9

    def resume(self) -> None:
        names, self.paused = self.paused, []
        for name in names:
            self._ask(["playerctl", "-p", name, "play"], name)
        self._at = -1e9
