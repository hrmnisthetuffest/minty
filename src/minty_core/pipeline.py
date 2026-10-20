import asyncio
import contextlib
import difflib
import time
from collections import deque
import logging
import re

import math

import numpy as np

from minty_core.audio.endpointer import rms
from minty_core.audio.gate import HALLUCINATIONS, judge
from minty_core.audio.stable import StablePrefix
from minty_core.audio.types import FRAME_S, Transcription
from minty_core.llm.types import DeadlineExceeded, LLMError
from minty_core.policy import is_affirmative  # noqa: F401 (also imported from here)
from minty_core.text import is_repeat
from minty_core.trace import NullTracer, turn_scope
from minty_core.voice.notes import is_stop as is_notes_stop
from minty_core.voice.turnhold import looks_complete

log = logging.getLogger(__name__)
BRAIN_DOWN = "I can't reach my brain right now."
MODELS_SLOW = "My models are not answering right now."  # the turn's first-token deadline passed
ONE_MOMENT = "One moment."  # said once when a turn has been quiet for turn.notice_after_s
STILL_WORKING = "Still working on it."  # said once when the request is repeated while it is being worked on
UNCLEAR = "Sorry, I couldn't make that out."
STOCK_PHRASES = (ONE_MOMENT, STILL_WORKING, MODELS_SLOW, BRAIN_DOWN, UNCLEAR)  # synthesised once at start


def _dbfs(value: float) -> float:
    """Level relative to full scale: 0 dB is the loudest possible, about -60 dB is silence."""
    return round(20 * math.log10(max(value, 1.0) / 32768), 1)


def caption_every(base_frames: int, cost_s: float) -> int:
    """Frames between live-caption refreshes: the configured interval, but at least long enough that captions take no more than
    half of the time (a slow recogniser used to be kept busy back to back while you spoke, competing with the real transcription)."""
    return max(base_frames, min(100, math.ceil(2 * cost_s / FRAME_S)))



def _quiet_task(task: asyncio.Task) -> None:
    """A guess nobody waited for must not leave "exception was never retrieved" in the log."""
    if not task.cancelled():
        task.exception()


class VoicePipeline:
    def __init__(self, *, audio_in, wake, endpointer, stt, tts, player, agent, bus, store, commands=None, captions=None, live=None, music=None, on_restart=None, tracer=None,
                 speech_check=None, on_conversation=None, streaming=None):
        self.audio_in, self.wake, self.endpointer = audio_in, wake, endpointer
        self.stt, self.tts, self.player = stt, tts, player
        self.agent, self.bus, self.store = agent, bus, store
        self.commands = commands
        self.music = music  # MusicWatcher or None
        self.on_restart = on_restart  # called after the spoken goodbye when the user asked to restart Minty
        self.live = live  # LiveMode or None
        self._stable = StablePrefix()
        self.on_conversation = on_conversation  # called for every real conversation (the day-start routine)
        self.captions = captions  # bytes -> str, sync; used for live partial text
        self.streaming = streaming  # SherpaStreaming or None: real word-by-word captions while you speak
        self.muted = False
        self.ptt = None  # voice.ptt.PushToTalk or None: hold a key to talk (opt-in)
        self.turn_judge = None  # async text -> True (finished) / False (still going) / None; voice.turnjudge
        self.focus = None  # focus.Focus or None: while it runs, non-urgent spoken notices wait
        self.recorder = None  # productivity.recorder.Recorder: gets frames only inside an explicit session
        self.voice = None  # voice.extras.VoiceExtras or None: corrections, dictation, pronunciations, controls
        self._last_request = ""
        self._unspoken: deque = deque()  # sentences of this answer not yet fully spoken, for "go on"
        self._answer_sentences: list[str] = []
        self._silent_follow = False
        self.stopper = None  # a detector of stop phrases ("stop", "wait") used while speaking, with echo cancellation
        self._hard_stop = False
        self._answered = False
        self.tracer = tracer or getattr(agent, "tracer", None) or NullTracer()
        self._turn: dict | None = None  # timings of the turn in progress, for the trace
        self.speech_check = speech_check  # pcm -> ms of real speech (None: no detector)
        self.noise_ignored = 0  # recordings dropped as noise since start (shown in the terminal)
        self._spoken: deque = deque(maxlen=8)  # (time, normalised text) of what Minty just said aloud
        self.state = "idle"
        self._queue: asyncio.Queue | None = None
        self._answer_started = False  # this reply has produced (or failed with) something to say
        self._wait_clips: list = []  # wait notices queued this reply ("one moment", "still working"): not answers
        self._stock: dict[str, tuple[str, object]] = {}  # stock phrase -> (voice settings it was made with, clip)
        self.ready = asyncio.Event()  # set by the app once models are loaded and stock phrases made
        self._still_said = False
        self._inflight: tuple[str, float] | None = None  # the request being worked on, and when it was made
        self._barge_text = ""  # what was said when a new request cut in, so it is not asked for again
        self._slow: list[bool] = []  # recent answered turns: was the first audio over the alarm threshold?
        self._alarmed = False
        self._barged = asyncio.Event()
        self._watcher: asyncio.Task | None = None
        self._notices = bus.subscribe({"notify"})  # level events would crowd out notices
        self._subscribed = True
        self.last_busy = 0.0
        self._notifying = False
        self._wake_at: float | None = None
        self._caption_cost_s = 0.0  # how long one live caption took last time (the refresh interval stretches to fit)
        self._speech_at: float | None = None  # when the wake word was recognised, for the latency trace
        self._spec: asyncio.Task | None = None  # recognition of the words so far, started in the pause (audio.speculative_stt)
        self._listen_now = False  # a button or key asked to start listening: the next frame begins a turn, no name needed
        self._early = False  # the last recording ended early because the sentence was plainly finished
        self._quiet_end_ms: int | None = None  # how long the pause had lasted when the last recording ended
        self._ack: tuple[str, object] | None = None  # (text, audio) of the cached wake acknowledgement
        agent.confirm = self.confirm
        try:
            agent.on_tool = self._on_tool
        except AttributeError:
            pass

    def _busy_state(self) -> str:
        """What working on a request looks like: the thinking display belongs to Thinking mode only; an ordinary
        request is just 'processing'."""
        return "thinking" if getattr(self.agent, "tier", "fast") == "deep" else "processing"

    def _on_tool(self, name: str | None) -> None:
        if self.state in ("processing", "thinking", "executing"):
            self._set_state("executing" if name else self._busy_state())

    async def _acknowledge(self) -> None:
        """Say the short acknowledgement after a bare "Minty", from a cached recording, then drop the frames the
        microphone heard meanwhile so the reply is not taken for your words."""
        cfg = self.store.config.audio
        text = cfg.wake_ack.strip()
        if cfg.earcons != "chime" and not text:
            return
        try:
            if cfg.earcons == "chime":
                from minty_core.voice.earcons import tone

                audio = tone("wake")
                self._set_state("speaking")
                await self.player.play(audio)
            else:
                if self._ack is None or self._ack[0] != text:
                    self._ack = (text, await self.tts.synthesize(text))
                audio = self._ack[1]
                self._set_state("speaking")
                self._remember_spoken(text)
                await self.player.play(audio)
            seconds = len(getattr(audio, "pcm", b"")) / 2 / max(1, getattr(audio, "rate", 16000))
            for _ in range(int(seconds / FRAME_S) + 2):  # what the mic heard while we spoke, plus a short echo
                await self._read()
        except Exception:
            log.warning("could not say the wake acknowledgement", exc_info=True)

    async def warm_up(self) -> None:
        """Prepare the acknowledgement and the stock phrases once at start, so they play without a pause."""
        text = self.store.config.audio.wake_ack.strip()
        if text and self._ack is None:
            try:
                self._ack = (text, await self.tts.synthesize(text))
            except Exception:
                log.warning("could not prepare the wake acknowledgement", exc_info=True)
        for phrase in STOCK_PHRASES:
            try:
                await self._stock_audio(phrase)
            except Exception:
                log.warning("could not prepare %r", phrase, exc_info=True)

    async def _stock_audio(self, text: str):
        """A stock phrase's clip, made again if the voice settings changed since."""
        voice = self.store.config.tts.model_dump_json()
        hit = self._stock.get(text)
        if hit is not None and hit[0] == voice:
            return hit[1]
        audio = await self.tts.synthesize(text)
        self._stock[text] = (voice, audio)
        return audio

    async def _synth(self, text: str):
        return await (self._stock_audio(text) if text in STOCK_PHRASES else self.tts.synthesize(text))

    def _set_state(self, state: str) -> None:
        self.state = state
        if self.voice is not None and state != "speaking":
            self.voice.unduck()
        if state not in ("idle", "loading"):
            self.last_busy = time.monotonic()  # for the maintenance window: "the microphone was just used"
        self.bus.publish("state", state=state)

    def idle_for(self) -> float:
        """Seconds since the microphone was last in use (large when it never was)."""
        return time.monotonic() - self.last_busy if self.last_busy else 1e9

    async def run(self) -> None:
        self.bus.attach_loop(asyncio.get_running_loop())
        if not self._subscribed:  # a restart under the supervisor: the last run unsubscribed on its way out
            self._notices = self.bus.subscribe({"notify"})
            self._subscribed = True
        await self.audio_in.start()
        notify_task = asyncio.create_task(self._notify_loop())
        try:
            while True:
                frame = await self._read()
                if self.muted or self._notifying:
                    self._listen_now = False
                    continue
                pressed = False
                manual, self._listen_now = self._listen_now, False
                if manual:
                    pass  # a button or key started this turn: no name, no held key
                elif self._ptt_active():
                    if not self.ptt.held:
                        continue  # key up: the frame is dropped here and kept nowhere
                    pressed = True
                elif not await asyncio.to_thread(self.wake.detect, frame):
                    continue
                self._wake_at = time.perf_counter()
                first_words = "" if manual or pressed else getattr(self.wake, "remainder", "")
                if not manual and not pressed:
                    self.wake.reset()
                paused = await self._pause_music()
                live = self.live is not None and self.live.available() and not pressed
                if not first_words and not live and not pressed and not manual:  # a button press needs no "Yes, sir?"
                    await self._acknowledge()
                try:
                    if self.live is not None and self.live.available():
                        self._set_state("live")
                        self._count_conversation()
                        handled = await self.live.converse(first_words, self._read)
                        self._set_state("idle")
                        if getattr(self.live, "start_noting", False):
                            self.live.start_noting = False
                            if self.voice is not None:
                                await self._note_loop()  # "note for me" said in a live conversation
                            continue
                        if handled and getattr(self.live, "continue_classic", False):
                            self.live.continue_classic = False
                            await self._turn_loop()  # left live mode mid-conversation: carry on in classic
                            continue
                        if handled:
                            continue
                    await self._turn_loop(first_words)
                    if self.commands is not None and getattr(self.commands, "switch_voice", None):
                        mode, self.commands.switch_voice = self.commands.switch_voice, None
                        if mode == "live" and self.live is not None and self.live.available():
                            self._set_state("live")  # "live mode" said in classic: start talking live right away
                            await self.live.converse("", self._read)
                            self._set_state("idle")
                        elif mode == "classic":
                            await self._turn_loop()  # and "classic mode" keeps the conversation going too
                finally:
                    if paused:
                        await asyncio.to_thread(self.music.resume)
        except EOFError:
            return
        finally:
            notify_task.cancel()
            await asyncio.gather(notify_task, return_exceptions=True)
            self.bus.unsubscribe(self._notices)
            self._subscribed = False
            await self.audio_in.stop()

    def listen_now(self) -> bool:
        """Start listening without the name (the Listen button or key). While an answer is being spoken it cuts in, like
        interrupting. False when there was nothing to do: muted, already listening, or busy in a way that cannot be cut in on."""
        if self.muted:
            return False
        if self.state == "idle" and not self._notifying:
            self._listen_now = True
            self._set_state("listening")  # at once: the display says so before the next audio frame arrives
            return True
        if self._queue is not None and self.state in ("speaking", "thinking", "executing", "processing"):
            self._barged.set()
            return True
        return False

    async def _notify_loop(self) -> None:
        held: list[str] = []
        while True:
            if self.focus is None:
                event = await self._notices.get()
            else:
                try:  # wake up to give back what was held when focus ends
                    event = await asyncio.wait_for(self._notices.get(), 1.0 if held else None)
                except asyncio.TimeoutError:
                    event = None
                if event is not None and event.get("type") == "notify" and self.focus.holds(event):
                    held.append(str(event.get("text", "")))
                    continue
                if held and not self.focus.active():
                    digest = "While you were focused: " + " ".join(held)
                    held = []
                    if event is not None:
                        self._notices.put_nowait(event)  # handle it right after the digest
                    event = {"type": "notify", "text": digest}
                elif event is None:
                    continue
            if event.get("type") != "notify":
                continue
            handed_over = False
            while self.muted or self.state != "idle":
                inject = getattr(self.live, "inject", None)
                if self.state == "live" and not self.muted and inject is not None and await inject(event["text"]):
                    handed_over = True  # Gemini says it inside the conversation
                    break
                await asyncio.sleep(0.2)
            if handed_over:
                continue
            self._notifying = True
            self._set_state("speaking")
            try:
                self.bus.publish("transcript", role="assistant", text=event["text"])
                await self._speak_plain(event["text"])
            finally:
                self._notifying = False
                self._set_state("idle")

    async def _turn_loop(self, first_words: str = "") -> None:
        try:
            again = True
            while again:
                self._answered = False
                again = await self._one_turn(first_words)
                first_words, self._barge_text = self._barge_text, ""
                if not again and self._answered and self.store.config.audio.follow_up and not self._ptt_active():
                    if not self._silent_follow:
                        await self._settle()
                    self._silent_follow = False
                    again = True  # listen once more; silence ends the conversation
        except EOFError:
            raise
        except Exception as e:
            log.exception("turn failed")
            self.bus.publish("error", message=str(e))
            self._set_state("error")  # shown briefly, so a failure never looks like quiet waiting
            await asyncio.sleep(1.5)
        finally:
            self._set_state("idle")

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", text.lower()))

    def _remember_spoken(self, text: str) -> None:
        norm = self._norm(text)
        if norm:
            self._spoken.append((time.monotonic(), norm))

    def _is_echo(self, text: str) -> bool:
        """True when a 'transcript' is really Minty's own voice coming back through the microphone."""
        heard = self._norm(text)
        if not heard:
            return False
        now = time.monotonic()
        for at, said in self._spoken:
            if now - at > 45:
                continue
            if heard in said or (len(said) >= 12 and said in heard):
                return True
            if difflib.SequenceMatcher(None, heard, said).ratio() >= 0.8:
                return True
        return False

    async def _pause_music(self) -> bool:
        cfg = self.store.config.audio
        if self.music is None or not (cfg.music_aware and cfg.music_pause):
            return False
        try:
            await asyncio.to_thread(self.music.pause)  # a hung player must not stall listening
        except Exception as e:
            log.debug("could not pause media: %s", e)
            return False
        return bool(self.music.paused)

    async def _read(self) -> bytes:
        frame = await self.audio_in.read()
        if self.recorder is not None and self.recorder.recording and not self.muted and frame:
            self.recorder.feed(frame)  # inside a session you started, and never while the microphone is muted
        if len(frame) % 2 == 0 and frame:  # every frame (12.5/s) so the panel meter feels live
            level = rms(frame)
            peak = int(np.abs(np.frombuffer(frame, dtype=np.int16).astype(np.int32)).max())
            threshold = getattr(self.endpointer, "threshold", None) or self.store.config.audio.speech_rms_threshold
            self.bus.publish("level", rms=int(level), db=_dbfs(level), peak_db=_dbfs(peak),
                             threshold_db=_dbfs(threshold), speech=level >= threshold)
        return frame

    async def _partial(self, pcm: bytes) -> None:
        started = time.perf_counter()
        try:
            text = await asyncio.to_thread(self.captions, pcm)
            self._caption_cost_s = time.perf_counter() - started  # how long a caption takes on this machine
        except Exception as e:  # captions are a nicety; never break a turn over them
            log.debug("live caption failed: %s", e)
            return
        self._show_caption(text)

    def _show_caption(self, text: str) -> None:
        """Publish a caption that only grows; stock phrases heard in noise never reach the screen."""
        phrase = " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))
        if not phrase or phrase in HALLUCINATIONS:
            return
        committed, tentative = self._stable.feed(text)
        if committed or tentative:
            self.bus.publish("partial", text=committed, tentative=tentative)

    def _show_guess(self, text: str) -> None:
        """The recognition made in the pause is the newest and fullest caption: settle it on the screen."""
        phrase = " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))
        if phrase and phrase not in HALLUCINATIONS:
            self.bus.publish("partial", text=self._stable.final(text), tentative="")

    async def _settle(self) -> None:
        """Skip the tail of our own speech so it is not heard as a follow-up."""
        for _ in range(int(0.8 / FRAME_S)):  # let the room's echo of our own voice die down
            await self._read()

    async def _one_turn(self, text: str = "") -> bool:
        """Returns True when the user barged in and a new turn should start immediately.

        `text` is whatever was already said after the wake word, if anything."""
        turn = {"t0": time.perf_counter(), "outcome": "silence"}
        self._turn = turn
        with turn_scope():  # everything this turn records shares one id
            try:
                return await self._one_turn_inner(text, turn)
            finally:
                self._drop_spec()
                self._turn = None
                self._trace_turn(turn)

    async def _transcribe(self, pcm: bytes) -> Transcription:
        detailed = getattr(self.stt, "transcribe_detailed", None)
        if detailed is not None:
            return await detailed(pcm)
        return Transcription(await self.stt.transcribe(pcm))

    def _drop_spec(self) -> None:
        spec, self._spec = self._spec, None
        if spec is not None and not spec.done():
            spec.cancel()

    async def _heard(self, pcm: bytes) -> Transcription:
        """The words of this recording: the guess made during the pause when there is one (the pause only added silence), else a
        fresh recognition. A guess that failed or was dropped is not a failed turn."""
        spec, self._spec = self._spec, None
        if spec is not None:
            await asyncio.wait({spec})
            if not spec.cancelled() and spec.exception() is None:
                if self._turn is not None:
                    self._turn["spec"] = "early" if self._early else "used"
                return spec.result()
        return await self._transcribe(pcm)

    def _ignore_noise(self, turn: dict, reason: str, text: str = "") -> bool:
        """Not speech after all: stay silent, count it, note why."""
        self.noise_ignored += 1
        turn["outcome"], turn["reason"] = "noise", reason
        log.info("ignored noise (%s)%s", reason, f": {text!r}" if text else "")
        return False

    def _trace_turn(self, turn: dict) -> None:
        t0 = turn.pop("t0")

        def ms(key):
            return round((turn.pop(key) - t0) * 1000, 1) if key in turn else None

        first_audio = ms("t_first_audio")
        self.tracer.event("turn", mode="classic", stt_ms=ms("t_stt"), first_sentence_ms=ms("t_first_sentence"),
                          first_audio_ms=first_audio, total_ms=round((time.perf_counter() - t0) * 1000, 1),
                          **turn)
        if turn.get("outcome") == "answered" and first_audio is not None:
            self._check_latency(first_audio)

    def _check_latency(self, first_audio_ms: float) -> None:
        """One notice when several replies in a row were slow to start; quiet again after a quick one."""
        cfg = self.store.config.turn
        if not cfg.latency_alarm_ms:
            return
        self._slow = (self._slow + [first_audio_ms > cfg.latency_alarm_ms])[-cfg.latency_alarm_turns:]
        if len(self._slow) == cfg.latency_alarm_turns and all(self._slow):
            if not self._alarmed:
                self._alarmed = True
                message = (f"Replies are slow: the last {cfg.latency_alarm_turns} took over "
                           f"{cfg.latency_alarm_ms / 1000:g} s to start speaking.")
                log.warning(message)
                self.tracer.event("latency_alarm", turns=cfg.latency_alarm_turns, over_ms=cfg.latency_alarm_ms)
                self.bus.publish("notice", message=message)
        elif not self._slow[-1]:
            self._alarmed = False

    async def _one_turn_inner(self, text: str, turn: dict) -> bool:
        if not text:
            self._set_state("listening")
            if self._wake_at is not None:  # how long from "Minty" until it was really listening
                turn["wake_to_listen_ms"] = round((time.perf_counter() - self._wake_at) * 1000, 1)
                self._wake_at = None
            pcm = await (self._record_ptt() if self._ptt_active() else self._record(speculate=True))
            if not pcm:
                return False
            turn["t0"] = time.perf_counter()  # latency is measured from the end of speech
            if self._quiet_end_ms is not None:
                turn["silence_ms"] = self._quiet_end_ms  # the pause that ended the turn (the biggest fixed part of the wait)
            if self._speech_at is not None:
                turn["capture_ms"] = round((turn["t0"] - self._speech_at) * 1000, 1)
            self._set_state(self._busy_state())
            voiced = await asyncio.to_thread(self.speech_check, pcm) if self.speech_check is not None else None
            turn["voiced_ms"] = voiced
            if voiced is not None and voiced < self.store.config.audio.min_voiced_ms:
                return self._ignore_noise(turn, f"only {voiced} ms of speech")
            try:
                heard = await self._heard(pcm)
                text = heard.text.strip()
            except Exception as e:
                log.warning("speech recognition failed: %s", e)
                self.bus.publish("error", message=f"speech recognition failed: {e}")
                turn["outcome"] = "stt_error"
                await self._speak_plain(UNCLEAR)
                return False
            turn["t_stt"] = time.perf_counter()
            if self.voice is not None:
                self.voice.heard(voiced, len(text.split()), getattr(self.endpointer, "noise_floor", None))
            verdict = judge(text, no_speech_prob=heard.no_speech_prob, avg_logprob=heard.avg_logprob,
                            voiced_ms=voiced, min_voiced_ms=self.store.config.audio.min_voiced_ms)
            if not verdict.accept:
                return self._ignore_noise(turn, verdict.reason, text)
            if self._is_echo(text) and not (self.voice is not None and self.voice.is_control(text)):
                log.info("ignored what sounded like my own voice: %r", text)
                turn["outcome"] = "echo"
                return False
            if self.store.config.audio.smart_turn:
                text = await self._finish_sentence(text, turn)
        else:
            self._wake_at = None
            self._set_state(self._busy_state())
        turn["words"] = len(text.split())
        self._count_conversation()
        self.bus.publish("partial", text="")
        self.bus.publish("transcript", role="user", text=text)
        if self.voice is not None:
            act = self.voice.intercept(text, self._last_request)
            if act is not None:
                if act.reply is not None:
                    turn["outcome"] = "command"
                    reply = act.reply
                    if act.then is not None:
                        reply = await act.then()
                    if reply:
                        self._set_state("speaking")
                        self.bus.publish("transcript", role="assistant", text=reply)
                        await self._speak_plain(reply)
                    if act.start_noting:
                        await self._note_loop()
                        return False
                    if act.keep_listening:
                        self._answered, self._silent_follow = True, not reply  # dictation: no wake word needed
                    return False
                if act.text is not None:
                    if act.drop_last:
                        from minty_core.voice.corrections import drop_last_turn

                        drop_last_turn(getattr(self.agent, "history", []))
                    text = act.text
                    self.bus.publish("transcript", role="user", text=text)
        reply = self.commands.handle(text) if self.commands is not None else None
        if reply is not None:
            turn["outcome"] = "command"
            self._set_state("speaking")
            self.bus.publish("transcript", role="assistant", text=reply)
            await self._speak_plain(reply)
            if getattr(self.commands, "pending_restart", False) and self.on_restart is not None:
                self.commands.pending_restart = False
                self.on_restart()
            return False  # a command is not a conversation: no follow-up window
        self._last_request = text
        barged = await self._respond(text)
        if self._hard_stop:  # "stop": the speech ended and nothing new begins
            self._hard_stop, self._answered = False, False
            turn["outcome"] = "stopped"
            return False
        self._answered = not barged
        turn["outcome"] = "barged" if barged else "answered"
        return barged

    def _publish_notes(self) -> None:
        notes = self.voice.notes
        self.bus.publish("notes", active=notes.active,
                         items=notes.recent(self.store.config.voice.notes_hud_lines))

    async def _note_loop(self) -> None:
        """"Note for me": every utterance is written down, with no wake word, until "Minty, stop noting". Silence does
        not end it, and nothing said here reaches the assistant."""
        notes = self.voice.notes
        notes.begin()
        self._publish_notes()
        try:
            while notes.active:
                if self.muted:
                    await self._read()  # keep draining the microphone; nothing is kept while muted
                    continue
                self._set_state("listening")
                pcm = await self._record(max_wait_s=30.0)
                if not pcm:
                    continue
                voiced = await asyncio.to_thread(self.speech_check, pcm) if self.speech_check is not None else None
                if voiced is not None and voiced < self.store.config.audio.min_voiced_ms:
                    continue
                try:
                    heard = await self._transcribe(pcm)
                except Exception as e:  # noqa: BLE001 - one bad clip must not end the session
                    log.warning("speech recognition failed while noting: %s", e)
                    continue
                text = heard.text.strip()
                verdict = judge(text, no_speech_prob=heard.no_speech_prob, avg_logprob=heard.avg_logprob,
                                voiced_ms=voiced, min_voiced_ms=self.store.config.audio.min_voiced_ms)
                if not verdict.accept:
                    continue
                if is_notes_stop(text):
                    break
                if self._is_echo(text):
                    continue
                if notes.add(text):
                    self.bus.publish("transcript", role="user", text=text)
                    self._publish_notes()
        finally:
            count = notes.end()
            self._publish_notes()
            self._set_state("idle")
        await self._speak_plain(f"Stopped noting. {count} {'note' if count == 1 else 'notes'} saved.")

    def _count_conversation(self) -> None:
        if self.on_conversation is None:
            return
        try:
            line = self.on_conversation()
        except Exception:  # noqa: BLE001 — bookkeeping must never break a conversation
            log.warning("counting the conversation failed", exc_info=True)
            return
        if line:
            self.bus.publish("notify", text=line)

    async def _finish_sentence(self, text: str, turn: dict) -> str:
        """The sentence sounds unfinished: wait up to turn_hold_ms for more (twice at most) and join it. When the
        word rules call it finished but it has no full stop, a small model may say it is still going (turn_judge):
        then wait longer, turn_judge_hold_ms."""
        from minty_core.voice.turnhold import unfinished
        from minty_core.voice.turnjudge import needs_judge

        audio = self.store.config.audio
        for _ in range(2):
            hold = audio.turn_hold_ms / 1000
            if not unfinished(text):
                if not (audio.turn_judge and self.turn_judge is not None and needs_judge(text)):
                    break
                started = time.perf_counter()
                verdict = await self.turn_judge(text)
                turn["judged"] = {True: "complete", False: "incomplete"}.get(verdict, "unsure")
                turn["judge_ms"] = round((time.perf_counter() - started) * 1000)
                if verdict is not False:
                    break
                hold = audio.turn_judge_hold_ms / 1000
            try:
                pcm = await self._record(max_wait_s=hold)
                if not pcm:
                    break
                more = (await self._transcribe(pcm)).text.strip()
            except Exception as e:  # noqa: BLE001 — never lose the first part over the second
                log.debug("could not hear the rest of the sentence: %s", e)
                break
            if not more or self._is_echo(more):
                break
            text = f"{text} {more}"
            turn["held"] = turn.get("held", 0) + 1
        return text

    def _ptt_active(self) -> bool:
        return self.ptt is not None and self.ptt.active()

    async def _record_ptt(self, wait_s: float = 8.0) -> bytes:
        """Frames from the key going down to it coming up (plus a short tail); nothing before the press is kept."""
        self._speech_at = None
        limit = self.store.config.audio.max_record_s
        start = time.monotonic()
        while not self.ptt.held:
            if time.monotonic() - start > wait_s:
                return b""
            await asyncio.sleep(0.02)
        frames: list[bytes] = []
        tail = 0
        for _ in range(int(limit / FRAME_S)):
            frames.append(await self._read())
            if not self.ptt.held:
                tail += 1
                if tail >= 2:
                    break
        self._speech_at = time.perf_counter()
        return b"".join(frames)

    async def _record(self, max_wait_s: float | None = None, speculate: bool = False) -> bytes:
        self._drop_spec()
        self._speech_at = None
        self._early, self._quiet_end_ms = False, None
        self.endpointer.reset()
        self._stable.reset()
        frames: list[bytes] = []
        audio_cfg = self.store.config.audio
        live = self.captions is not None and audio_cfg.live_captions
        every = max(1, round(audio_cfg.caption_interval_ms / 1000 / FRAME_S))  # frames between caption refreshes, at least
        caption: asyncio.Task | None = None
        stream = None
        spec: asyncio.Task | None = None
        spec_runs, spec_shown = 0, None
        listen_in_pause = speculate and audio_cfg.speculative_stt
        if self.streaming is not None and audio_cfg.live_captions:
            try:
                stream = self.streaming.new_stream()
            except Exception:  # noqa: BLE001
                log.warning("streaming captions failed to start; using chunked captions", exc_info=True)
                self.streaming = None
        try:
            for _ in range(int(audio_cfg.max_record_s / FRAME_S)):
                frame = await self._read()
                frames.append(frame)
                if max_wait_s is not None and not self.endpointer.heard_speech and \
                        len(frames) * FRAME_S >= max_wait_s:
                    self.endpointer.feed(frame)
                    break  # nothing more is coming
                quiet = getattr(self.endpointer, "quiet_ms", None) or 0
                if stream is not None:
                    try:
                        text = self.streaming.accept(stream, frame)
                        if self.endpointer.heard_speech:
                            self._show_caption(text)
                    except Exception:  # noqa: BLE001 — off for the session; chunked captions carry on
                        log.warning("streaming captions failed; using chunked captions", exc_info=True)
                        self.streaming, stream = None, None
                elif (live and self.endpointer.heard_speech and not (listen_in_pause and quiet)  # in a pause the guess is the caption
                      and len(frames) % caption_every(every, self._caption_cost_s) == 0
                      and (caption is None or caption.done())):
                    caption = asyncio.create_task(self._partial(b"".join(frames)))
                done = self.endpointer.feed(frame)
                if self._speech_at is None and self.endpointer.heard_speech:
                    self._speech_at = time.perf_counter()
                if listen_in_pause and self.endpointer.heard_speech and not done:
                    quiet = getattr(self.endpointer, "quiet_ms", None)
                    if quiet is not None:
                        if quiet == 0 and spec is not None:
                            spec.cancel()  # they carried on: the guess is stale
                            spec = None
                        elif spec is None and quiet >= audio_cfg.spec_after_ms and spec_runs < 2:
                            spec = asyncio.create_task(self._transcribe(b"".join(frames)))
                            spec.add_done_callback(_quiet_task)
                            spec_runs += 1
                        elif (spec is not None and audio_cfg.early_finish_ms and quiet >= audio_cfg.early_finish_ms
                              and spec.done() and not spec.cancelled() and spec.exception() is None
                              and looks_complete(spec.result().text)):
                            done, self._early = True, True  # a finished sentence and a pause: no need to wait the full silence
                if (spec is not None and spec is not spec_shown and spec.done() and not spec.cancelled()
                        and spec.exception() is None):
                    spec_shown = spec
                    self._show_guess(spec.result().text)
                if done:
                    break
        finally:
            if caption is not None:
                caption.cancel()
                await asyncio.gather(caption, return_exceptions=True)
            if spec is not None:
                if self.endpointer.heard_speech and not spec.cancelled():
                    self._spec = spec  # the caller uses it as the words of this recording
                else:
                    spec.cancel()
            self._quiet_end_ms = getattr(self.endpointer, "quiet_ms", None)
        return b"".join(frames) if self.endpointer.heard_speech else b""

    async def _respond(self, text: str) -> bool:
        self._barged.clear()
        self._unspoken.clear()
        self._answer_sentences = []
        if self.voice is not None:
            self.voice.clear_tail()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._queue = queue
        self._answer_started, self._wait_clips, self._still_said = False, [], False
        self._inflight = (text, time.monotonic())
        produce = asyncio.create_task(self._produce(text, queue))
        consume = asyncio.create_task(self._consume(queue))
        barge_wait = asyncio.create_task(self._barged.wait())
        after = self.store.config.turn.notice_after_s
        notice = asyncio.create_task(self._notice(queue, after)) if after > 0 else None
        if self.store.config.audio.barge_in:
            self._start_watcher()
        failure = None
        try:
            await asyncio.wait({consume, barge_wait}, return_when=asyncio.FIRST_COMPLETED)
            barged = self._barged.is_set()
            if consume.done() and not consume.cancelled():
                failure = consume.exception()
        finally:
            tasks = [t for t in (produce, consume, barge_wait, notice) if t is not None]
            for task in tasks:
                task.cancel()
            await self._stop_watcher()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._stop_watcher()  # a cancelled confirm() may have restarted it
            self._queue = None
        if barged:
            if self.voice is not None:
                self.voice.interrupted(list(self._unspoken))  # what was not finished is kept for "go on"
            self.player.stop()
        if failure is not None:
            raise failure
        return barged

    async def _notice(self, queue: asyncio.Queue, after: float) -> None:
        """'One moment.' once, if the turn has said nothing by then (a slow model or a slow tool)."""
        await asyncio.sleep(after)
        if self._answer_started or self._still_said:
            return
        if self.store.config.turn.notice_skips_streaming and getattr(self.agent, "reply_started", False):
            return  # the first words are already coming: a clip now would only sit in front of the answer
        if await self._queue_notice(queue, ONE_MOMENT) and self._turn is not None:
            self._turn["notice"] = True

    async def _queue_notice(self, queue: asyncio.Queue, text: str) -> bool:
        try:
            audio = await self._synth(text)
        except Exception:
            log.warning("could not synthesise %r", text, exc_info=True)
            return False
        if self._answer_started or queue.full():  # the answer got there first
            return False
        self._wait_clips.append(audio)
        self._remember_spoken(text)
        queue.put_nowait(audio)
        return True

    async def _produce(self, text: str, queue: asyncio.Queue) -> None:
        try:
            async with contextlib.aclosing(self.agent.respond(text)) as replies:
                async for sentence in replies:
                    self._answer_started = True
                    if self._turn is not None:
                        self._turn.setdefault("t_first_sentence", time.perf_counter())
                    spoken = self.voice.prepare(sentence) if self.voice is not None else sentence
                    if self.voice is not None and hasattr(self.tts, "speed_factor"):
                        self.tts.speed_factor = self.voice.speed_factor()
                    audio = await self.tts.synthesize(spoken)
                    self._remember_spoken(sentence)
                    self.bus.publish("transcript", role="assistant", text=sentence)
                    self._unspoken.append(sentence)
                    self._answer_sentences.append(sentence)
                    await queue.put(audio)
        except LLMError as e:
            log.warning("no LLM available: %s", e)
            self.bus.publish("error", message=str(e))
            self._answer_started = True
            try:
                local = await self._offline_answer(text)
                if local:  # the internet is down: what needs no model is still answered
                    self._remember_spoken(local)
                    await queue.put(await self._synth(local))
                else:
                    await queue.put(await self._synth(MODELS_SLOW if isinstance(e, DeadlineExceeded)
                                                      else BRAIN_DOWN))
            except Exception:
                log.exception("could not speak the brain-down notice")
        except Exception as e:
            log.exception("reply failed")
            self.bus.publish("error", message=str(e))
        if self.voice is not None:
            self.voice.note_answer(self._answer_sentences)
        await queue.put(None)

    async def _offline_answer(self, text: str) -> str | None:
        reply = getattr(self.agent, "offline_reply", None)
        if reply is None:
            return None
        try:
            return await reply(text)
        except Exception:  # noqa: BLE001
            log.warning("offline answer failed", exc_info=True)
            return None

    async def _consume(self, queue: asyncio.Queue) -> None:
        while True:
            audio = await queue.get()
            try:
                if audio is None:
                    return
                self._set_state("speaking")
                if self._turn is not None and not any(audio is n for n in self._wait_clips):  # notices are not answers
                    self._turn.setdefault("t_first_audio", time.perf_counter())
                await self.player.play(self._voiced(audio))
                if not any(audio is n for n in self._wait_clips) and self._unspoken:
                    self._unspoken.popleft()  # that sentence was spoken to the end
            finally:
                queue.task_done()

    def _start_watcher(self) -> None:
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.create_task(self._watch_key() if self._ptt_active() else self._watch())

    async def _watch_key(self) -> None:
        """Push-to-talk: pressing the key while Minty speaks is the interruption."""
        await self.ptt.wait_press()
        self._barged.set()

    async def _stop_watcher(self) -> None:
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, EOFError):
                await watcher

    def _stop_enabled(self) -> bool:
        cfg = self.store.config.audio
        return self.stopper is not None and cfg.hands_free_stop and cfg.aec

    async def _watch(self) -> None:
        try:
            while True:
                frame = await self._read()
                if self._stop_enabled() and await asyncio.to_thread(self.stopper.detect, frame):
                    self.stopper.reset()
                    self._hard_stop = True
                    self._barged.set()
                    return
                if await asyncio.to_thread(self.wake.detect, frame):
                    self.wake.reset()
                    if self.store.config.turn.coalesce_repeats and not self._answer_started:
                        heard = await self._hear_while_working()
                        if not heard:
                            continue
                        if self._is_repeat(heard):
                            await self._still_working()
                            continue
                        self._barge_text = heard  # a different request: answer it without asking again
                    self._barged.set()
                    return
        except EOFError:
            return

    async def _hear_while_working(self) -> str:
        """The name was said before any answer: listen to what follows, while the request keeps running."""
        self._set_state("listening")
        try:
            pcm = await self._record()
            if not pcm:
                return ""
            heard = (await self._transcribe(pcm)).text.strip()
            if heard and self._is_echo(heard):  # the microphone caught our own "one moment" or answer
                log.info("ignored what sounded like my own voice: %r", heard)
                return ""
            return heard
        except Exception as e:  # noqa: BLE001 — a failed check must not break the request in flight
            log.warning("could not hear the interruption: %s", e)
            return ""
        finally:
            if self.state == "listening":
                self._set_state(self._busy_state())

    def _is_repeat(self, heard: str) -> bool:
        cfg = self.store.config
        if self._inflight is None or time.monotonic() - self._inflight[1] > cfg.turn.repeat_window_s:
            return False
        wake = tuple(w.lower() for w in cfg.audio.wake_phrases)
        return is_repeat(self._inflight[0], heard, wake_words=wake, threshold=cfg.turn.repeat_overlap)

    async def _still_working(self) -> None:
        log.info("request repeated while in progress; not restarting it")
        if self._turn is not None:
            self._turn["repeats"] = self._turn.get("repeats", 0) + 1
        if self._still_said or self._queue is None:
            return
        self._still_said = True
        await self._queue_notice(self._queue, STILL_WORKING)

    def _voiced(self, audio):
        """Level and ducking for whatever is about to play (whisper mode, the room, the evening, music)."""
        if self.voice is None:
            return audio
        self.voice.duck()
        return self.voice.shape(audio)

    async def _speak_plain(self, text: str) -> None:
        self._remember_spoken(text)
        try:
            await self.player.play(self._voiced(await self._synth(text)))
        except Exception:
            log.exception("could not speak %r", text)

    async def say(self, text: str) -> None:
        await self._speak_plain(text)

    async def confirm(self, prompt: str) -> bool:
        if self._queue is not None:
            await self._queue.join()
        await self._speak_plain(prompt)
        await self._stop_watcher()
        try:
            self._set_state("listening")
            pcm = await (self._record_ptt(wait_s=15.0) if self._ptt_active() else self._record())
            if not pcm:
                return False
            answer = await self.stt.transcribe(pcm)
        finally:
            if self._queue is not None and self.store.config.audio.barge_in:
                self._start_watcher()
        return is_affirmative(answer)
