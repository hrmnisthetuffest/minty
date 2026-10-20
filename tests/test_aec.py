"""Echo cancellation through PipeWire's module (502) and the hands-free stop (503)."""
import asyncio
import os

from tests.test_pipeline import TurnTracer, build, wait_until
from minty_core.voice.aec import SINK, SOURCE, EchoCancel


class Pactl:
    def __init__(self, loaded=False, fail_load=False):
        self.calls, self.loaded, self.fail_load = [], loaded, fail_load

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:3] == ["pactl", "list", "short"]:
            return 0, ("5\tmodule-echo-cancel\tsource_name=minty_aec_source sink_name=minty_aec_sink\n"
                       if self.loaded else "1\tmodule-alsa-card\t\n")
        if argv[:2] == ["pactl", "load-module"]:
            if self.fail_load:
                return 1, "Failure: Module initialization failed"
            self.loaded = True
            return 0, "42\n"
        if argv[:2] == ["pactl", "unload-module"]:
            self.loaded = False
            return 0, ""
        if argv[:2] == ["pactl", "info"]:
            return 0, "Server Name: PulseAudio (on PipeWire 1.0.5)\n"
        return 1, "unknown"


def test_status_reflects_whether_the_module_is_loaded():
    assert EchoCancel(Pactl(loaded=False)).status().loaded is False
    st = EchoCancel(Pactl(loaded=True)).status()
    assert st.loaded and st.module_id == "5" and st.source == SOURCE and st.sink == SINK


def test_enable_loads_the_webrtc_module_once_with_our_own_names():
    run = Pactl()
    aec = EchoCancel(run)
    result = aec.enable()
    assert result.ok and result.module_id == "42"
    load = [c for c in run.calls if c[:2] == ["pactl", "load-module"]][0]
    assert "module-echo-cancel" in load and "aec_method=webrtc" in load
    assert f"source_name={SOURCE}" in load and f"sink_name={SINK}" in load
    again = aec.enable()  # already there: nothing is loaded twice
    assert again.ok and sum(1 for c in run.calls if c[:2] == ["pactl", "load-module"]) == 1


def test_a_failed_load_says_why_and_changes_nothing():
    aec = EchoCancel(Pactl(fail_load=True))
    r = aec.enable()
    assert r.ok is False and "initialization failed" in r.message
    assert aec.status().loaded is False


def test_disable_unloads_only_our_module():
    run = Pactl(loaded=True)
    r = EchoCancel(run).disable()
    assert r.ok and ["pactl", "unload-module", "5"] in run.calls
    assert EchoCancel(Pactl(loaded=False)).disable().ok is True  # nothing to do


def test_the_process_environment_points_minty_at_the_virtual_nodes_and_nothing_else(monkeypatch):
    for k in ("PULSE_SOURCE", "PULSE_SINK"):
        monkeypatch.delenv(k, raising=False)
    aec = EchoCancel(Pactl(loaded=True))
    assert aec.apply_environment() is True
    assert os.environ["PULSE_SOURCE"] == SOURCE and os.environ["PULSE_SINK"] == SINK
    for k in ("PULSE_SOURCE", "PULSE_SINK"):
        monkeypatch.delenv(k)
    assert EchoCancel(Pactl(loaded=False)).apply_environment() is False and "PULSE_SOURCE" not in os.environ


class FakeStopper:
    def detect(self, frame):
        return frame == b"STOP"

    def reset(self):
        pass


def _build(tmp_path, hands_free=True, aec=True):
    p, f = build(tmp_path, stt_texts=["tell me a story"], replies=[["Once upon a time.", "There was a lamp.",
                                                                   "The end."]], player_delay=0.3)
    data = p.store.config.model_dump(mode="json")
    data["audio"].update(hands_free_stop=hands_free, aec=aec, barge_in=True)
    p.store.update(data)
    p.stopper = FakeStopper()
    p.tracer = TurnTracer()
    return p, f


async def test_a_spoken_stop_halts_the_speech_and_does_not_start_another_turn(tmp_path):
    p, f = _build(tmp_path)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Once upon a time." in f.player.played)
    f.audio.push(b"STOP")
    await wait_until(lambda: p.state == "idle" and f.player.stopped >= 1)
    f.audio.close()
    await asyncio.wait_for(task, 2)
    assert b"The end." not in f.player.played
    assert f.agent.heard == ["tell me a story"] and len(f.stt.calls) == 1  # no second turn began
    assert any(e.get("outcome") == "stopped" for e in p.tracer.events if e["kind"] == "turn")


async def test_without_aec_or_with_the_flag_off_the_stop_phrase_is_ignored(tmp_path):
    for hands_free, aec in ((False, True), (True, False)):
        p, f = _build(tmp_path / f"{hands_free}{aec}", hands_free=hands_free, aec=aec)
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        task = asyncio.create_task(p.run())
        await wait_until(lambda: b"Once upon a time." in f.player.played)
        f.audio.push(b"STOP")
        await wait_until(lambda: len(f.player.played) == 3)
        f.audio.close()
        await asyncio.wait_for(task, 3)
        assert b"The end." in f.player.played
