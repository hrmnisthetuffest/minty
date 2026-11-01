"""If Minty dies while the full-screen display is open (a failed start, an unexpected error), the terminal must not be left reporting mouse
movements as text (the shell then fills with `0;45;8M0;1;1m...`) or in single-key mode. The restore is registered to run at exit, not only on
the display's own clean shutdown."""
import asyncio
import atexit
import io
import os
import sys

import pytest

pty = pytest.importorskip("pty")  # a terminal emulator: Windows has neither pty nor termios
termios = pytest.importorskip("termios")

from minty_core.tui import TuiModel, _KeyReader


async def test_the_terminal_is_put_back_even_when_only_the_exit_handlers_run(monkeypatch):
    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r")
    out = io.StringIO()
    registered = []
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn) or fn)
    before = termios.tcgetattr(slave)
    reader = _KeyReader(TuiModel(), lambda line: asyncio.sleep(0), None, mouse=lambda: True)
    reader.start()
    try:
        assert termios.tcgetattr(slave)[3] & termios.ECHO == 0  # single-key mode is on
        assert _KeyReader.MOUSE_ON in out.getvalue() and registered, "nothing was registered to undo it"
        asyncio.get_running_loop().remove_reader(slave)  # what a dead event loop no longer offers the exit handler
        out.truncate(0)
        out.seek(0)
        for fn in registered:  # the interpreter's exit: no display clean-up has run
            fn()
        assert _KeyReader.MOUSE_OFF in out.getvalue()
        assert termios.tcgetattr(slave)[3] & termios.ECHO == before[3] & termios.ECHO
    finally:
        os.close(master)
        stdin.close()


async def test_stopping_cleanly_still_works_and_nothing_is_done_twice(monkeypatch):
    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r")
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", out)
    reader = _KeyReader(TuiModel(), lambda line: asyncio.sleep(0), None, mouse=lambda: True)
    reader.start()
    reader.stop()
    off = out.getvalue().count(_KeyReader.MOUSE_OFF)
    reader._restore_terminal()  # the exit handler after a clean stop
    assert off == 1 and out.getvalue().count(_KeyReader.MOUSE_OFF) == 1
    os.close(master)
    stdin.close()
