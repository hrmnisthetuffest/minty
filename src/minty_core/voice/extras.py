"""What the voice pipeline asks of the WP-08 features, in one place: the pipeline calls three methods and each feature
is behind its own `voice.*` switch."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import threading
from datetime import datetime

from minty_core.audio.endpointer import rms  # noqa: F401  (re-exported for callers measuring levels)
from minty_core.audio.types import Audio
from minty_core.nightmode import scale_pcm
from minty_core.voice.adapt import (WHISPER_GAIN, Ducker, Pronunciations, ambient_gain, pace_factor, time_profile)
from minty_core.voice.corrections import Vocabulary, drop_last_turn, parse
from minty_core.voice.notes import NoteTaker, is_start as is_notes_start

log = logging.getLogger(__name__)
START_DICTATION = {"start dictation", "take dictation", "dictation mode", "dictate a note", "begin dictation",
                   "start dictating"}
END_DICTATION = {"stop dictation", "end dictation", "done dictating", "finish dictation", "stop dictating"}


@dataclass
class Intercept:
    reply: str | None = None  # say this instead of asking the assistant ("" = handled silently)
    text: str | None = None  # ask this instead of what was heard
    drop_last: bool = False
    then: object = None  # an async callable returning the sentence to say (dictation saved its note)
    keep_listening: bool = False  # carry on listening without the wake word (dictation)
    start_noting: bool = False  # the pipeline now runs the noting loop (voice/notes.py) until "Minty, stop noting"


class VoiceExtras:
    def __init__(self, store, state_dir: Path):
        self.store = store
        self.dir = Path(state_dir)
        self.vocabulary = Vocabulary(self.dir / "voice-vocabulary.json")
        self.pronunciations = Pronunciations(self.dir / "voice-pronunciations.json")
        self.ducker = Ducker()
        self._noise_db: float | None = None
        self._wps: float | None = None
        self._now = datetime.now
        self.save_note = None  # async (text) -> reply; set by the app (the journal)
        self.dictating = False
        self.notes = NoteTaker(self.dir / "voice-notes.md")
        self._dictation: list[str] = []
        self._tail: list[str] = []
        self._last_answer = ""
        self._last_note = ""

    @property
    def cfg(self):
        return self.store.config.voice

    # ---- what the pipeline tells us -------------------------------------------------------------
    def interrupted(self, unspoken: list[str]) -> None:
        self._tail = [t for t in unspoken if t.strip()]
        self._tail_at = time.monotonic()

    def take_recent_tail(self, max_age_s: float = 45.0) -> list[str]:
        """The rest of an answer that was cut off a moment ago, once; an older one is dropped, never brought back."""
        tail, self._tail = self._tail, []
        return tail if tail and time.monotonic() - getattr(self, "_tail_at", 0.0) <= max_age_s else []

    def clear_tail(self) -> None:
        self._tail = []

    def note_answer(self, sentences: list[str]) -> None:
        if sentences:
            self._last_answer = " ".join(sentences)

    # ---- phrases ---------------------------------------------------------------------------------
    def _config(self, section: str, key: str, value) -> None:
        data = self.store.config.model_dump(mode="json")
        data[section][key] = value
        self.store.update(data)

    def intercept(self, text: str, last_request: str) -> Intercept | None:
        cfg = self.cfg
        phrase = " ".join(re.sub(r"[?!.,']", " ", text.lower()).split())
        if self.dictating:
            return self._dictate(text, phrase)
        if cfg.notes and is_notes_start(text):
            return Intercept(reply="Noting. I'm writing everything down until you tell me to stop.", start_noting=True)
        if cfg.dictation and phrase in START_DICTATION:
            self.dictating, self._dictation = True, []
            return Intercept(reply="Dictating. Say stop dictation when you're done.", keep_listening=True)
        if cfg.playback_controls:
            act = self._playback(phrase)
            if act is not None:
                return act
        act = self._settings(phrase, text)
        if act is not None:
            return act
        if cfg.corrections:
            fix = parse(text, last_request)
            if fix is not None and last_request:
                if cfg.vocabulary:
                    self.vocabulary.learn(last_request, fix.text)
                return Intercept(text=fix.text, drop_last=True)
        return None

    def _dictate(self, text: str, phrase: str) -> Intercept:
        if phrase in END_DICTATION:
            self.dictating = False
            note, self._dictation = " ".join(self._dictation).strip(), []

            async def finish() -> str:
                if not note:
                    return "Nothing was dictated."
                if self.save_note is None:
                    return "I have nowhere to save a note right now."
                return await self.save_note(note)

            async def finish_and_remember() -> str:
                self._last_note = note
                return await finish()

            return Intercept(reply="", then=finish_and_remember)
        self._dictation.append(text.strip())
        return Intercept(reply="", keep_listening=True)  # said back to nobody: it is a note, never a command

    def _playback(self, phrase: str) -> Intercept | None:
        if phrase in ("go on", "continue", "carry on", "keep going", "resume", "go ahead and finish"):
            if not self._tail:
                return Intercept(reply="I haven't got anything left to say.")
            tail, self._tail = self._tail, []
            return Intercept(reply=" ".join(tail))
        if phrase in ("skip", "skip that", "skip it", "next", "next bit", "skip this bit"):
            if not self._tail:
                return Intercept(reply="There's nothing left to skip.")
            self._tail = self._tail[1:]
            rest, self._tail = " ".join(self._tail), []
            return Intercept(reply=rest or "That was the last bit.")
        if phrase in ("repeat that", "say that again", "repeat", "what was that", "say it again", "come again"):
            return Intercept(reply=self._last_answer or "I haven't said anything yet.")
        if phrase in ("shorter", "make it shorter", "shorter please", "short version", "the short version",
                      "give me the short version", "in short"):
            return Intercept(text="Say that again in a short version: one or two sentences.")
        speed = self.store.config.tts.speed
        if phrase in ("slower", "speak slower", "slow down", "talk slower", "a bit slower"):
            self._config("tts", "speed", round(max(0.6, speed - 0.1), 2))
            return Intercept(reply="Slower.")
        if phrase in ("faster", "speak faster", "speed up", "talk faster", "a bit faster"):
            self._config("tts", "speed", round(min(1.6, speed + 0.1), 2))
            return Intercept(reply="Faster.")
        return None

    def _settings(self, phrase: str, original: str) -> Intercept | None:
        from minty_core.voice.adapt import parse_pronunciation

        cfg = self.cfg
        if phrase in ("whisper mode", "whisper mode on", "whisper", "speak quietly", "turn on whisper mode"):
            self._config("voice", "whisper", True)
            return Intercept(reply="Whisper mode on.")
        if phrase in ("whisper mode off", "stop whispering", "speak normally", "speak up", "turn off whisper mode"):
            self._config("voice", "whisper", False)
            return Intercept(reply="Normal voice.")
        pers = self.store.config.personality
        table = {
            ("be brief", "keep it short", "shorter answers", "brief answers"): ("verbosity", 1, "Short answers."),
            ("more detail", "more detail please", "be more detailed", "give me more detail"):
                ("verbosity", 7, "More detail from now on."),
            ("normal length", "normal answers", "answer normally"): ("verbosity", 3, "Normal length."),
            ("no jokes", "no humour", "no humor"): ("humor", 0, "No jokes."),
        }
        for phrases, (key, value, reply) in table.items():
            if phrase in phrases:
                self._config("personality", key, value)
                return Intercept(reply=reply)
        steps = {
            ("be more formal", "more formal", "be formal"): ("formality", 2, "More formal."),
            ("be less formal", "less formal", "be casual", "more casual"): ("formality", -2, "More relaxed."),
            ("make it funnier", "more humour", "more humor", "be funnier", "more jokes"): ("humor", 3, "Funnier."),
            ("less humour", "less humor", "less funny", "fewer jokes"): ("humor", -2, "Drier."),
        }
        for phrases, (key, step, reply) in steps.items():
            if phrase in phrases:
                self._config("personality", key, max(0, min(10, getattr(pers, key) + step)))
                return Intercept(reply=reply)
        if phrase in ("read that back", "read it back", "read my note back", "read the note back"):
            return Intercept(reply=self._last_note or self._last_answer or "I have nothing to read back.")
        if phrase in ("read this page to me", "read the article", "read the page aloud", "read this page",
                      "read the page", "read this article to me", "read the article aloud"):
            return Intercept(text="Read the current page aloud. Use the browser reading tool, skip menus, adverts and "
                                  "comments, and speak only the main article text, in order, without adding comments.")
        if phrase in ("switch to hindi", "hindi mode", "understand hindi", "hindi mode on", "speak hindi",
                      "listen for hindi"):
            self._config("voice", "bilingual", True)
            data = self.store.config.model_dump(mode="json")
            data["stt"].update(language="", allowed_languages=["en", "hi"])
            self.store.update(data)
            return Intercept(reply="Hindi mode: I'll listen for Hindi and English. I don't have a Hindi voice "
                                   "installed, so I'll still speak in the English voice; ask for text on the screen "
                                   "if that doesn't work for you.")
        if phrase in ("english only", "hindi mode off", "stop listening for hindi", "english mode"):
            self._config("voice", "bilingual", False)
            data = self.store.config.model_dump(mode="json")
            data["stt"].update(language="en", allowed_languages=["en"])
            self.store.update(data)
            return Intercept(reply="English only.")
        teach = parse_pronunciation(original)
        if teach is not None and cfg.pronunciations:
            try:
                self.pronunciations.add(*teach)
            except ValueError:
                return None
            return Intercept(reply=f"Okay, I'll say {teach[0]} as {teach[1]}.")
        return None

    def is_control(self, text: str) -> bool:
        """The phrase that ends dictation is also in what Minty says to start it; it must not be taken for an echo."""
        phrase = " ".join(re.sub(r"[?!.,']", " ", text.lower()).split())
        return self.dictating and phrase in END_DICTATION

    def stt_hint(self) -> str:
        return self.vocabulary.hint() if self.cfg.vocabulary else ""

    # ---- speaking ---------------------------------------------------------------------------------
    def prepare(self, sentence: str) -> str:
        """The text sent to the voice: your pronunciations applied. What is shown and logged stays the real sentence."""
        return self.pronunciations.apply(sentence) if self.cfg.pronunciations else sentence

    def _profile(self) -> dict:
        return time_profile(self.cfg.time_profiles, self._now()) if self.cfg.time_of_day else {"gain": 1.0,
                                                                                               "speed": 1.0}

    def gain(self) -> float:
        g = WHISPER_GAIN if self.cfg.whisper else 1.0
        if self.cfg.ambient_volume:
            g *= ambient_gain(self._noise_db)
        g *= self._profile()["gain"]
        return max(0.2, min(2.0, g))

    def shape(self, audio: Audio) -> Audio:
        g = self.gain()
        if abs(g - 1.0) < 0.01 or len(audio.pcm) % 2:
            return audio
        return Audio(scale_pcm(audio.pcm, g), audio.rate)

    def speed_factor(self) -> float:
        f = self._profile()["speed"]
        if self.cfg.pace_matching:
            f *= pace_factor(self._wps)
        return max(0.7, min(1.3, f))

    def heard(self, voiced_ms: float | None, words: int, noise_floor: float | None) -> None:
        """What the room and your pace were like on this utterance (for volume and pace matching)."""
        import math

        if noise_floor:
            self._noise_db = round(20 * math.log10(max(noise_floor, 1.0) / 32768), 1)
        if voiced_ms and voiced_ms >= 600 and words >= 3:
            wps = words / (voiced_ms / 1000)
            self._wps = wps if self._wps is None else 0.6 * self._wps + 0.4 * wps

    def duck(self) -> None:
        if self.cfg.duck_music:
            threading.Thread(target=self.ducker.begin, daemon=True).start()

    def unduck(self) -> None:
        if self.ducker._saved:
            threading.Thread(target=self.ducker.restore, daemon=True).start()
