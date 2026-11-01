"""The look and feel of the setup: a banner, numbered steps, arrow-key menus and quiet status lines, on `rich`.

The setup talks to a small `UI` (say / ok / warn / ask / confirm / choose / multi / working), so the same steps run in a
terminal and in a test. `RichUI` is the terminal one: arrow keys and space when the terminal supports them, plain
numbered questions otherwise (a pipe, a dumb terminal, Windows). Keys typed as secrets are never echoed.
"""
from __future__ import annotations

import getpass
import os
import sys
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Iterator, Protocol

from minty_core import osutil
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

EMBER = ("#7a0b0b", "#a31212", "#c8321b", "#e5531f", "#ff7a2f", "#ffa24a")  # the banner runs from deep red to ember
ACCENT, GOOD, WARN, BAD, DIM = "#ff7a2f", "#4caf50", "#e0a030", "#e5484d", "grey58"

BANNER = (
    "███╗   ███╗██╗███╗   ██╗████████╗██╗   ██╗",
    "████╗ ████║██║████╗  ██║╚══██╔══╝╚██╗ ██╔╝",
    "██╔████╔██║██║██╔██╗ ██║   ██║    ╚████╔╝",
    "██║╚██╔╝██║██║██║╚██╗██║   ██║     ╚██╔╝",
    "██║ ╚═╝ ██║██║██║ ╚████║   ██║      ██║",
    "╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝   ╚═╝      ╚═╝",
)


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    hint: str = ""
    disabled: bool = False  # shown, but cannot be chosen (the hint says why)


class UI(Protocol):
    def banner(self, tagline: str) -> None: ...
    def step(self, n: int, total: int, title: str, blurb: str = "") -> None: ...
    def say(self, text: str) -> None: ...
    def ok(self, text: str) -> None: ...
    def warn(self, text: str) -> None: ...
    def fail(self, text: str) -> None: ...
    def note(self, title: str, body: str) -> None: ...
    def table(self, title: str, rows: list[tuple[str, str]]) -> None: ...
    def ask(self, prompt: str, default: str = "", secret: bool = False) -> str: ...
    def confirm(self, prompt: str, default: bool = True) -> bool: ...
    def choose(self, prompt: str, options: list[Option], default: str = "") -> str: ...
    def multi(self, prompt: str, options: list[Option], defaults: tuple[str, ...] = ()) -> list[str]: ...
    def working(self, text: str): ...  # a context manager


@contextmanager
def cbreak_mode() -> Iterator[None]:
    """Keys arrive one at a time without Enter while a menu is open, and the terminal is put back after.
    Held for the whole menu, not per key, so keys typed quickly are not lost between two reads. (Windows reads each key straight from the console: nothing to switch.)"""
    if osutil.IS_WINDOWS:
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    """One key press (the terminal must be in cbreak mode): 'up', 'down', 'enter', 'space', 'esc' or the character."""
    if osutil.IS_WINDOWS:
        return _read_key_windows()
    import select

    fd = sys.stdin.fileno()
    ch = os.read(fd, 1).decode("utf-8", "ignore")
    if ch == "\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            rest = os.read(fd, 2).decode("utf-8", "ignore")
            return {"[A": "up", "[B": "down"}.get(rest, "esc")
        return "esc"
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch


def _read_key_windows() -> str:
    import msvcrt

    first = msvcrt.getwch()
    text = osutil.windows_key_text(first, msvcrt.getwch() if first in ("\x00", "\xe0") else "")
    if text in ("\r", "\n"):
        return "enter"
    if text == " ":
        return "space"
    if text == "\x03":
        raise KeyboardInterrupt
    if text == "\x1b":
        return "esc"
    return {"\x1b[A": "up", "\x1b[B": "down"}.get(text, text if len(text) == 1 else "")


class RichUI:
    def __init__(self, console: Console | None = None, *, keys=None, interactive: bool | None = None):
        self.console = console or Console(highlight=False)
        self._keys, self._injected = keys or read_key, keys is not None
        if interactive is None:
            try:
                if not osutil.IS_WINDOWS:
                    import termios  # noqa: F401
                interactive = sys.stdin.isatty() and self.console.is_terminal and os.environ.get("TERM") != "dumb"
            except ImportError:
                interactive = False
        self.interactive = interactive

    # -- looks --------------------------------------------------------------------------------------------------
    def banner(self, tagline: str) -> None:
        c = self.console
        c.print()
        for i, line in enumerate(BANNER):
            c.print(Text(line, style=f"bold {EMBER[min(i, len(EMBER) - 1)]}"), justify="left")
        c.print(Text(tagline, style=f"italic {DIM}"))
        c.print()

    def step(self, n: int, total: int, title: str, blurb: str = "") -> None:
        c = self.console
        dots = Text()
        for i in range(1, total + 1):
            dots.append("● " if i < n else "◉ " if i == n else "○ ", style=ACCENT if i <= n else DIM)
        c.print()
        c.print(Rule(Text(f" {n}/{total}  {title} ", style=f"bold {ACCENT}"), style=EMBER[1]))
        c.print(dots)
        if blurb:
            c.print(Text(blurb, style=DIM))
        c.print()

    def say(self, text: str) -> None:
        self.console.print(Text(text))

    def ok(self, text: str) -> None:
        self.console.print(Text.assemble(("  ✓ ", f"bold {GOOD}"), text))

    def warn(self, text: str) -> None:
        self.console.print(Text.assemble(("  ! ", f"bold {WARN}"), text))

    def fail(self, text: str) -> None:
        self.console.print(Text.assemble(("  ✗ ", f"bold {BAD}"), text))

    def note(self, title: str, body: str) -> None:
        self.console.print(Panel(Text(body), title=Text(title, style=f"bold {ACCENT}"), border_style=EMBER[2],
                                 padding=(1, 2), expand=False))

    def table(self, title: str, rows: list[tuple[str, str]]) -> None:
        t = Table(title=title, title_style=f"bold {ACCENT}", border_style=EMBER[2], show_header=False, padding=(0, 2))
        for k, v in rows:
            t.add_row(Text(k, style=DIM), Text(v))
        self.console.print(t)

    @contextmanager
    def working(self, text: str) -> Iterator[None]:
        if self.console.is_terminal:
            with self.console.status(Text(text, style=ACCENT), spinner="dots"):
                yield
        else:
            self.console.print(Text(f"  … {text}"))
            yield

    # -- questions ----------------------------------------------------------------------------------------------
    def ask(self, prompt: str, default: str = "", secret: bool = False) -> str:
        shown = f"  {prompt}{f' [{default}]' if default else ''}: "
        self.console.print(Text(shown, style=ACCENT), end="")
        value = getpass.getpass("") if secret else input()
        return value.strip() or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        answer = self.ask(f"{prompt} ({'Y/n' if default else 'y/N'})", "").lower()
        return default if not answer else answer.startswith("y")

    def choose(self, prompt: str, options: list[Option], default: str = "") -> str:
        usable = [o for o in options if not o.disabled]
        if not usable:
            return ""
        start = next((i for i, o in enumerate(options) if o.value == default and not o.disabled),
                     next(i for i, o in enumerate(options) if not o.disabled))
        if not self.interactive:
            return self._numbered(prompt, options, default=options[start].value)
        return options[self._menu(prompt, options, start, None)[0]].value

    def multi(self, prompt: str, options: list[Option], defaults: tuple[str, ...] = ()) -> list[str]:
        if not self.interactive:
            return self._numbered_many(prompt, options, defaults)
        chosen = {i for i, o in enumerate(options) if o.value in defaults and not o.disabled}
        _, picked = self._menu(prompt, options, 0, chosen)
        return [options[i].value for i in sorted(picked)]

    def _line(self, o: Option, cursor: bool, mark: str) -> Text:
        t = Text()
        t.append("  ❯ " if cursor else "    ", style=f"bold {ACCENT}")
        t.append(mark, style=ACCENT)
        t.append(o.label, style=(DIM if o.disabled else "bold" if cursor else ""))
        if o.hint:
            t.append(f"  {o.hint}", style=DIM)
        t.truncate(max(20, self.console.width - 1), overflow="ellipsis")
        return t

    def _menu(self, prompt: str, options: list[Option], start: int, chosen: set[int] | None) -> tuple[int, set[int]]:
        c, cursor, many = self.console, start, chosen is not None
        chosen = set(chosen or ())
        c.print(Text(f"  {prompt}", style="bold"), Text("(↑/↓, space to tick, enter to confirm)" if many
                                                    else "(↑/↓ and enter)", style=DIM))
        first = True
        with (nullcontext() if self._injected else cbreak_mode()):
            while True:
                if not first:
                    c.file.write(f"\x1b[{len(options)}A\x1b[J")
                first = False
                for i, o in enumerate(options):
                    mark = ("[x] " if i in chosen else "[ ] ") if many else ""
                    c.print(self._line(o, i == cursor, mark), no_wrap=True, overflow="ellipsis", crop=True)
                key = self._keys()
                if key in ("up", "k"):
                    cursor = (cursor - 1) % len(options)
                elif key in ("down", "j"):
                    cursor = (cursor + 1) % len(options)
                elif key == "space" and many and not options[cursor].disabled:
                    chosen ^= {cursor}
                elif key == "enter":
                    if many or not options[cursor].disabled:
                        return cursor, chosen
                elif key.isdigit() and 1 <= int(key) <= len(options):
                    cursor = int(key) - 1

    def _numbered(self, prompt: str, options: list[Option], default: str) -> str:
        self.console.print(Text(f"  {prompt}", style="bold"))
        for i, o in enumerate(options, 1):
            self.console.print(Text(f"    {i}) {o.label}" + (f"  {o.hint}" if o.hint else ""),
                                    style=DIM if o.disabled else ""))
        names = [o.value for o in options]
        while True:
            answer = self.ask(f"Number [{names.index(default) + 1}]", "")
            if not answer:
                return default
            if answer.isdigit() and 1 <= int(answer) <= len(options) and not options[int(answer) - 1].disabled:
                return options[int(answer) - 1].value
            self.warn("Type one of the numbers shown.")

    def _numbered_many(self, prompt: str, options: list[Option], defaults: tuple[str, ...]) -> list[str]:
        self.console.print(Text(f"  {prompt}", style="bold"))
        for i, o in enumerate(options, 1):
            tick = "x" if o.value in defaults else " "
            self.console.print(Text(f"    {i}) [{tick}] {o.label}" + (f"  {o.hint}" if o.hint else ""),
                                    style=DIM if o.disabled else ""))
        answer = self.ask("Numbers separated by commas, Enter for the ticked ones, 0 for none", "")
        if not answer:
            return [o.value for o in options if o.value in defaults and not o.disabled]
        picked = {int(x) for x in answer.replace(" ", "").split(",") if x.isdigit()}
        return [o.value for i, o in enumerate(options, 1) if i in picked and not o.disabled]
