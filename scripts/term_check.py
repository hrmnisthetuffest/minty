#!/usr/bin/env python3
"""Does this terminal show pictures? Asks it, says what it answered, and draws a small test picture if it can.

  python scripts/term_check.py            (from the repository, with the project's Python)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minty_core.gfx import _ask_terminal, cell_pixels, encode_sixel, parse_reply  # noqa: E402


def main() -> None:
    print(f"TERM={os.environ.get('TERM')}  TERM_PROGRAM={os.environ.get('TERM_PROGRAM')}  VTE_VERSION={os.environ.get('VTE_VERSION')}")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("Run this directly in a terminal (not through a pipe).")
    reply = _ask_terminal()
    print("The terminal answered:", repr(reply) if reply else "nothing (no answer within 0.6 s)")
    protocol = parse_reply(reply)
    print("Pictures:", protocol or "not supported by this terminal")
    print("Cell size in pixels (width, height):", cell_pixels(sys.stdout.fileno()))
    if protocol == "sixel":
        w = h = 96
        rgb = b"".join(bytes((x * 255 // w, y * 255 // h, 160)) for y in range(h) for x in range(w))
        print("\nA test picture should appear below (a smooth gradient):")
        sys.stdout.write(encode_sixel(rgb, w, h) + "\n")
        sys.stdout.flush()
    elif protocol == "kitty":
        print("kitty graphics: supported. (Minty uses it automatically.)")
    else:
        print("\nThis terminal does not draw pictures, so Minty shows the pixel-block cover (nothing is broken). For the real "
              "cover use kitty (best choice), WezTerm or Ghostty; foot, Konsole, Windows Terminal and sixel-enabled "
              "VTE builds also work.")


if __name__ == "__main__":
    main()
