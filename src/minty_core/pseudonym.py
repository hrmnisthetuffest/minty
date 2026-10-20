"""Pseudonymise before cloud: providers that may train on prompts see PERSON_1, EMAIL_1, PHONE_1, ADDRESS_1
instead of the real thing; the reply (text and tool arguments) is mapped back on this machine. Names are the ones
you list in safety.pseudonym_names (no guessing); e-mails, phone numbers and street addresses are found by pattern.
"""
from __future__ import annotations

import re
from dataclasses import replace

from minty_core.llm.types import Message

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s-]?)?\d(?:[\s-]?\d){9,11}(?!\w)")
_ADDRESS = re.compile(r"\b\d{1,5}(?:\s+[A-Z][a-z]+){1,4}\s+(?:Road|Rd|Street|St|Lane|Ln|Avenue|Ave|Marg|Nagar|"
                      r"Colony|Sector|Block|Enclave|Vihar|Apartments?|Society|Drive|Way)\b")
_PLACEHOLDER = re.compile(r"\b(?:PERSON|EMAIL|PHONE|ADDRESS)_\d+\b")
_TAIL = re.compile(r"[A-Z][A-Z_0-9]*$")


class Pseudonymizer:
    def __init__(self, names=()):
        names = sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True)
        self._names = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE) \
            if names else None
        self._forward: dict[tuple[str, str], str] = {}  # (kind, normalised original) -> placeholder
        self.back: dict[str, str] = {}  # placeholder -> original as first seen
        self._counts: dict[str, int] = {}

    def _placeholder(self, kind: str, original: str) -> str:
        key = (kind, " ".join(original.lower().split()))
        if key not in self._forward:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            ph = f"{kind}_{self._counts[kind]}"
            self._forward[key] = ph
            self.back[ph] = original
        return self._forward[key]

    def mask(self, text: str) -> str:
        if not text:
            return text
        for kind, pattern in (("EMAIL", _EMAIL), ("PHONE", _PHONE), ("ADDRESS", _ADDRESS), ("PERSON", self._names)):
            if pattern is not None:
                text = pattern.sub(lambda m, k=kind: self._placeholder(k, m.group()), text)
        return text

    def unmask(self, text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self.back.get(m.group(), m.group()), text) if text else text

    def _map(self, value, fn):
        if isinstance(value, str):
            return fn(value)
        if isinstance(value, dict):
            return {k: self._map(v, fn) for k, v in value.items()}
        if isinstance(value, list):
            return [self._map(v, fn) for v in value]
        return value

    def unmask_args(self, args: dict) -> dict:
        return self._map(args, self.unmask)

    def mask_messages(self, messages: list[Message]) -> list[Message]:
        """Copies with every text and tool argument masked; the originals are left as they are."""
        out = []
        for m in messages:
            calls = [replace(c, arguments=self._map(c.arguments, self.mask)) for c in m.tool_calls]
            out.append(replace(m, content=self.mask(m.content), tool_calls=calls))
        return out


class StreamUnmasker:
    """Maps a streamed reply back, holding a trailing fragment that could still grow into a placeholder."""

    def __init__(self, p: Pseudonymizer):
        self._p, self._buf = p, ""

    def feed(self, text: str) -> str:
        self._buf += text
        m = _TAIL.search(self._buf)
        hold = ""
        if m:
            tail = m.group()
            if any(ph.startswith(tail) or (tail.startswith(ph) and tail[len(ph):].isdigit()) for ph in self._p.back):
                hold = tail
        ready, self._buf = self._buf[:len(self._buf) - len(hold)], hold
        return self._p.unmask(ready)

    def flush(self) -> str:
        ready, self._buf = self._buf, ""
        return self._p.unmask(ready)
