"""`minty instant`: list what is answered without a model, or try a phrase. Also writes docs/instant-answers.md from the rules."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.instant import CATEGORIES, RULES, Env, Instant
from minty_core.instant.actions import ACTIONS, CATEGORY as ACTION_CATEGORY, match as match_action

DOC = Path(__file__).resolve().parents[3] / "docs" / "instant-answers.md"
INTRO = """# Instant answers

Some requests need no thinking: the time, a sum, a conversion, a dice roll, the capital of a country. Minty answers these
**by itself, the moment you finish speaking**, with no model call, no network and no quota used. The same goes for a few
spoken commands (a timer, the volume, pausing the music), which run as one tool call without a model in between.

- A request is answered this way only when **all of it** is one of the questions below. Anything longer, vaguer or different
  goes to the model as usual, so "what is the time complexity of quicksort" is never answered with the clock.
- Try any phrase without speaking: `minty instant "what is 15 percent of 240"`. List everything: `minty instant`.
- Turn it off with `instant.enabled = false`, the commands only with `instant.actions = false`, or leave a whole category to the
  model with `instant.skip = ["fun and games"]` (names below). Changes apply at the next request.
- This page is written from the code (`python -m minty_core.instant.cli --write`), and a test fails if it is out of date.

"""


def _quote(phrases) -> str:
    return " · ".join(f"“{p}”" for p in phrases)


def catalog_markdown() -> str:
    total = sum(len(r.examples) for r in RULES) + sum(len(a.examples) for a in ACTIONS)
    out = [INTRO.rstrip("\n"), "", f"There are {len(RULES) + len(ACTIONS)} groups and {total}+ example phrases below; each group "
           "understands many more wordings than the examples.", "", "## Categories", ""]
    for category in CATEGORIES:
        rules = [r for r in RULES if r.category == category]
        out.append(f"- **{category}**: {', '.join(r.name for r in rules)}")
    out.append(f"- **{ACTION_CATEGORY}** (commands): {', '.join(a.name for a in ACTIONS)}")
    for category in CATEGORIES:
        out += ["", f"## {category}", ""]
        for rule in (r for r in RULES if r.category == category):
            out += [f"**{rule.name}**  ", _quote(rule.examples), ""]
    out += ["", f"## {ACTION_CATEGORY} (commands)", "",
            "These become one tool call, checked by the same safety rules as anything the model asks for. If the tool is not set up "
            "(for example Spotify is not connected), the request goes to the model instead.", ""]
    for action in ACTIONS:
        out += [f"**{action.name}**  ", _quote(action.examples), ""]
    return "\n".join(out).rstrip("\n") + "\n"


def run(words: list[str], clock: Callable[[], datetime] | None = None) -> int:
    """Try a phrase (exit 0 when it is answered here, 1 when the model would handle it) or list everything."""
    if not words:
        print(catalog_markdown())
        return 0
    text = " ".join(words)
    hit = Instant(Env(now=clock or datetime.now)).answer(text)
    if hit is not None:
        print(hit.reply)
        return 0
    action = match_action(text, now=clock() if clock else None)
    if action is not None:
        print(f"(command) {action.tool} {action.args}")
        return 0
    print("(this would go to the model)")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--write"]:
        DOC.write_text(catalog_markdown(), encoding="utf-8")
        print(f"wrote {DOC}")
        return 0
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
