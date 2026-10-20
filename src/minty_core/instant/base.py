"""The parts every instant-answer rule shares: what a rule is, what it is given, and what the computer can tell it."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Callable

from minty_core import __version__
from minty_core.instant.text import clean, words_to_digits


@dataclass
class Env:
    """Everything a rule may look at, so a test can fake any of it. Nothing here reaches the network."""
    now: Callable[[], datetime] = datetime.now
    rng: random.Random = field(default_factory=random.Random)
    name: str = "Minty"
    version: str = __version__
    wake_words: tuple[str, ...] = ()
    last_reply: Callable[[], str | None] = lambda: None  # the last thing said, for "say that again"
    last_request: Callable[[], str | None] = lambda: None  # the last thing asked, for "what did I just ask"
    now_playing: Callable[[], dict | None] | None = None  # title, artist, ... of the song playing; None when no player is known
    system: object | None = None  # a system.Machine; None means the real one
    started: float = field(default_factory=time.time)


@dataclass
class Query:
    text: str  # as said
    cmd: str  # cleaned: lower case, no wake word, no commas
    listy: str  # cleaned with the commas kept (for "pick between a, b or c")
    env: Env

    @cached_property
    def digits(self) -> str:
        """The request with number words turned into digits."""
        return words_to_digits(self.cmd)

    def pick(self, *options: str) -> str:
        return self.env.rng.choice(options)

    @classmethod
    def of(cls, text: str, env: Env) -> "Query":
        return cls(text, clean(text, env.wake_words), clean(text, env.wake_words, keep_commas=True), env)


@dataclass(frozen=True)
class Rule:
    name: str  # unique
    category: str  # shown in the catalog; a category can be left to the model with instant.skip
    examples: tuple[str, ...]  # things people say; each must be answered by this rule (a test checks it)
    run: Callable[[Query], str | None]


@dataclass(frozen=True)
class Hit:
    reply: str
    rule: str
    category: str
