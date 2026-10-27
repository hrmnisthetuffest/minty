"""Connects the instant answers to the assistant: the agent asks them before the model, and so does the fast path for actions."""
from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Callable

from minty_core.instant import Instant
from minty_core.instant import actions
from minty_core.instant.base import Env


def _history_text(agent, role: str, skip_newest: bool = False) -> str | None:
    for message in reversed(agent.history):
        if message.role != role or not message.content or getattr(message, "tool_calls", None):
            continue
        if skip_newest:
            skip_newest = False
            continue
        return message.content
    return None


def attach(agent, store, services: dict, clock: Callable[[], datetime] | None = None, rng: random.Random | None = None) -> Instant:
    """Give `agent` its instant answers. Settings (`instant.enabled`, `instant.skip`) and the wake words are read at each request,
    so changing them needs no restart."""
    env = Env(now=clock or datetime.now, rng=rng or random.Random(), started=time.time(),
              wake_words=tuple(store.config.audio.wake_phrases),
              last_reply=lambda: _history_text(agent, "assistant"),
              last_request=lambda: _history_text(agent, "user", skip_newest=True))

    def now_playing():  # the watcher is added to `services` after this runs, so it is looked up at each question
        watcher = services.get("music_watcher")
        return watcher.now_playing() if watcher is not None else None

    env.now_playing = now_playing
    instant = Instant(env)

    def local(text: str):
        cfg = store.config.instant
        if not cfg.enabled:
            return None
        env.wake_words = tuple(store.config.audio.wake_phrases)
        return instant.answer(text, skip=cfg.skip)

    agent.local_answer = local
    return instant


def with_actions(page_match: Callable[[str], object], store) -> Callable[[str], object]:
    """The agent's fast path: simple commands about the open page first, then timers, reminders, volume and music."""
    def match(text: str):
        hit = page_match(text)
        if hit is None:
            cfg = store.config.instant
            if cfg.enabled and cfg.actions:
                hit = actions.match(text, tuple(store.config.audio.wake_phrases), fuzzy=cfg.fuzzy)
        return hit

    return match
