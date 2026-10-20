"""Voice notes in, voice replies out (543) and the per-chat choice between them (734).

A voice note is turned into text and handled exactly like text you typed: the same agent, the same safety gate, no
extra trust (a voice message proves nothing about who sent it, so a class-3 action still needs the laptop). What you
said is never run as a slash command and never taken as a passphrase reply. A forwarded voice note is quoted as data.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.audio.types import Audio
from minty_core.telegram.api import MAX_DOWNLOAD, TelegramError
from minty_core.telegram.media import STT_RATE, CodecUnavailable, ogg_to_pcm, pcm_to_ogg

log = logging.getLogger(__name__)

MODES = ("auto", "on", "off")
SUMMARY_CHARS = 450


class ChatPrefs:
    """Small per-chat choices kept in one 0600 json file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, chat_id: int, key: str = "voice", default: str = "") -> str:
        return str(self._load().get(str(chat_id), {}).get(key, default))

    def set(self, chat_id: int, key: str, value: str) -> None:
        data = self._load()
        data.setdefault(str(chat_id), {})[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)


def spoken_summary(text: str) -> str:
    """The start of an answer, cut at a sentence end, short enough to listen to."""
    text = " ".join(text.split())
    if len(text) <= SUMMARY_CHARS:
        return text
    cut = text[:SUMMARY_CHARS]
    ends = [m.end() for m in re.finditer(r"[.!?](?:\s|$)", cut)]
    return cut[:ends[-1]].strip() if ends else cut.rsplit(" ", 1)[0].rstrip(",;:") + "."


class VoiceNotes:
    def __init__(self, gateway, stt, tts: Callable[[], object | None], prefs: ChatPrefs, *,
                 max_bytes: int = MAX_DOWNLOAD, memo: Callable[[str], Awaitable[str]] | None = None):
        self.gw, self.stt, self._tts, self.prefs, self.max_bytes = gateway, stt, tts, prefs, max_bytes
        self._memo = memo  # saves a transcript as a note (the notes vault later); None: memos are not offered

    def install(self) -> None:
        self.gw.media_hooks.append(self.on_message)
        self.gw.handlers["/voice"] = self.command
        if self._memo is not None:
            self.gw.handlers["/memo"] = self.memo_command
        self.gw.voice_out = self.speak

    # ---- the per-chat choice ----------------------------------------------------------------------
    def mode(self, chat_id: int) -> str:
        chosen = self.prefs.get(chat_id)
        return chosen if chosen in MODES else self.gw.store.config.telegram.voice_replies

    async def command(self, chat_id: int, args: str) -> str:
        word = args.strip().lower()
        if word not in MODES:
            return (f"Voice replies are {self.mode(chat_id)} here. /voice on = always, /voice off = never, "
                    "/voice auto = a voice note gets a voice answer, typing gets text.")
        self.prefs.set(chat_id, "voice", word)
        return f"Voice replies: {word}."

    async def memo_command(self, chat_id: int, args: str) -> str:
        word = args.strip().lower()
        if word not in ("on", "off"):
            on = self.prefs.get(chat_id, "memo") == "on"
            return (f"Memo mode is {'on' if on else 'off'}. /memo on = voice notes are saved as notes, not acted "
                    "on. /memo off = back to normal. A voice note captioned \"memo\" is saved once either way.")
        self.prefs.set(chat_id, "memo", word)
        return f"Memo mode: {word}."

    # ---- in ---------------------------------------------------------------------------------------
    async def on_message(self, chat_id: int, message: dict) -> bool:
        media = message.get("voice") or message.get("audio")
        if not media:
            return False
        gw = self.gw
        if gw._private_now():
            await gw.reply(chat_id, "I'm in private mode, so I won't take voice notes in Telegram. Use the laptop.")
            return True
        cap = gw.store.config.telegram.voice_max_s
        if int(media.get("file_size") or 0) > self.max_bytes or int(media.get("duration") or 0) > cap:
            await gw.reply(chat_id, f"That voice note is too big for me (20 MB, {cap // 60} minutes at most).")
            return True
        try:
            info = await gw.api.get_file(str(media.get("file_id")))
            data = await gw.api.download(str(info.get("file_path")), self.max_bytes)
            pcm = ogg_to_pcm(data, STT_RATE)
            text = (await self.stt.transcribe(pcm, STT_RATE) or "").strip()
        except (TelegramError, CodecUnavailable) as e:
            log.warning("voice note failed: %s", type(e).__name__)
            await gw.reply(chat_id, "I couldn't open that voice note. Try again, or type it.")
            return True
        except Exception:  # noqa: BLE001 — a speech-to-text failure is a message, not a crash of the gateway
            log.warning("voice note transcription failed", exc_info=True)
            await gw.reply(chat_id, "I couldn't transcribe that just now. Try again, or type it.")
            return True
        if not text:
            await gw.reply(chat_id, "I couldn't make out any words in that voice note.")
            return True
        caption = str(message.get("caption") or "").strip().lower().rstrip(".!")
        if self._memo is not None and (caption in ("memo", "note", "save") or self.prefs.get(chat_id, "memo") == "on"):
            forwarded = bool(message.get("forward_origin") or message.get("forward_date"))
            await gw.reply(chat_id, await self._memo(("Forwarded memo: " if forwarded else "") + text))
            return True
        await gw.reply(chat_id, f'Heard: "{text[:500]}"', silent=True)
        if message.get("forward_origin") or message.get("forward_date"):
            await gw._forwarded(chat_id, text, message)
        else:
            await gw.ask(chat_id, text, voice=True)
        return True

    # ---- out --------------------------------------------------------------------------------------
    async def speak(self, chat_id: int, answer: str, by_voice: bool) -> None:
        mode = self.mode(chat_id)
        if mode == "off" or (mode == "auto" and not by_voice):
            return
        tts = self._tts()
        if tts is None or not answer.strip():
            return
        try:
            audio: Audio = await tts.synthesize(spoken_summary(answer))
            ogg = pcm_to_ogg(audio.pcm, audio.rate)
            await self.gw.sender.send_media(chat_id, "voice", ogg, "voice.ogg", silent=True)
        except (TelegramError, CodecUnavailable) as e:
            log.warning("voice reply not sent: %s", type(e).__name__)
        except Exception:  # noqa: BLE001 — the text answer already went out; the voice is a bonus
            log.warning("voice reply failed", exc_info=True)
