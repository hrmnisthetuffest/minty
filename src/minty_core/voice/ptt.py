"""Push-to-talk (506): opt-in. While it is on the pipeline runs no wake detector, reads the microphone only to keep
the stream flowing and keeps none of it until the key is held.

Sources of a press: a global hotkey (`pynput`, optional), the control API (the panel, a phone), or anything that calls
`press()` and `release()`."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger(__name__)


class PushToTalk:
    def __init__(self, enabled: Callable[[], bool]):
        self._enabled = enabled
        self._pressed = asyncio.Event()

    def active(self) -> bool:
        return bool(self._enabled())

    @property
    def held(self) -> bool:
        return self._pressed.is_set()

    def press(self) -> None:
        if self.active():
            self._pressed.set()

    def release(self) -> None:
        self._pressed.clear()

    async def wait_press(self) -> None:
        while not self._pressed.is_set():
            await asyncio.sleep(0.02)


class PynputHotkey:
    """A global key listener on a thread; presses are handed to the loop. `available()` is False without pynput."""

    def __init__(self, key_name: str, ptt: PushToTalk, loop: asyncio.AbstractEventLoop):
        self.key_name, self._ptt, self._loop, self._listener = key_name.lower(), ptt, loop, None

    @staticmethod
    def available() -> bool:
        try:
            import pynput  # noqa: F401
        except Exception:  # noqa: BLE001 — also fails with no display
            return False
        return True

    def start(self) -> bool:
        if not self.available():
            return False
        from pynput import keyboard

        def name(key) -> str:
            return getattr(key, "name", None) or getattr(key, "char", None) or ""

        def on_press(key):
            if name(key).lower() == self.key_name:
                self._loop.call_soon_threadsafe(self._ptt.press)

        def on_release(key):
            if name(key).lower() == self.key_name:
                self._loop.call_soon_threadsafe(self._ptt.release)

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()
        return True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
