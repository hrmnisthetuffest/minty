"""Local fast path: a simple command about the open page ("click Store", "scroll down", "search for cats") becomes
one browser tool call with no model call. It only decides *which* tool and element; running it goes through the
agent's normal Guard/policy path, so "click Buy now" is still asked. Anything unclear returns None and the model
handles it as usual.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from minty_core.webpage import Node, elements

_LEAD = re.compile(r"^(?:(?:ok|okay|now|so|and|then|please|can you|could you|would you|will you|just)\b[\s,]*)+")
_TRAIL = re.compile(r"[\s,]*(?:please|for me)?[\s.!?]*$")
_CLICK = re.compile(r"^(?:click|tap|press|hit|select|choose|open)(?: on)?(?: the)? (?P<label>.+?)"
                    r"(?: (?:button|link|tab|option))?$")
_TYPE = re.compile(r"^(?:type|enter|write) (?P<text>.+?) (?:in|into)(?: the)? (?P<label>.+?)(?: (?:box|field))?$")
_SEARCH = re.compile(r"^search(?: for)? (?P<q>.+)$")
_SCROLL = re.compile(r"^scroll(?: the page)? (?P<dir>up|down)$")
_BACK = re.compile(r"^(?:go )?back$")
_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$")
_TYPABLE = {"textbox", "searchbox", "combobox"}


@dataclass
class FastAction:
    tool: str
    args: dict = field(default_factory=dict)
    say: str | Callable[[str], str] = ""  # what to say once it ran; a function gets the tool's result and returns the words


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w' .-]", " ", text.lower()).split()).strip(" .")


def _command(text: str, wake_words) -> str:
    out = " ".join(text.lower().replace(",", " , ").split())
    for w in wake_words:
        out = re.sub(rf"^{re.escape(w)}\b[\s,]*", "", out)
    out = _LEAD.sub("", out)
    out = _TRAIL.sub("", out)
    return " ".join(out.replace(" , ", ", ").split())


def _spoken(label: str) -> str:
    flat = " ".join(label.split())
    return flat if len(flat) <= 40 else flat[:39] + "…"


def _find(items: dict[str, Node], wanted: str, roles=None) -> Node | None:
    """The one element whose label is `wanted` (or its singular/plural); None if none or several."""
    target = _norm(wanted)
    if not target:
        return None
    forms = {target, target + "s", target[:-1] if target.endswith("s") else target}
    hits = [n for n in elements(list(items.values()))
            if (roles is None or n.role in roles) and _norm(n.label) in forms]
    return hits[0] if len(hits) == 1 else None


def match(text: str, items: dict[str, Node] | None, wake_words=()) -> FastAction | None:
    if not items or not text:
        return None
    cmd = _command(text, wake_words)
    if not cmd or "," in cmd:
        return None
    if m := _SCROLL.match(cmd):
        return FastAction("browser_scroll", {"direction": m["dir"]}, f"Scrolled {m['dir']}.")
    if _BACK.match(cmd):
        return FastAction("browser_back", {}, "Went back.")
    if m := _SEARCH.match(cmd):
        query = m["q"].strip()
        if " then " in f" {query} " or not query:
            return None
        boxes = [n for n in elements(list(items.values()))
                 if n.role == "searchbox" or (n.role in _TYPABLE and "search" in n.label.lower())]
        if len(boxes) != 1:
            return None
        return FastAction("browser_type", {"ref": boxes[0].ref, "text": query, "submit": True},
                          f"Searching for {_spoken(query)}.")
    if m := _TYPE.match(cmd):
        node = _find(items, m["label"], _TYPABLE)
        if node is None:
            return None
        return FastAction("browser_type", {"ref": node.ref, "text": m["text"]}, "Typed it.")
    if m := _CLICK.match(cmd):
        label = m["label"].strip()
        if cmd.startswith("open") and _DOMAIN.match(label):
            return FastAction("browser_open", {"url": label}, f"Opening {label}.")
        node = _find(items, label)
        if node is None:
            return None
        return FastAction("browser_click", {"ref": node.ref}, f"Clicked {_spoken(node.label)}.")
    return None
