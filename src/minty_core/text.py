import re

_END = re.compile(r"([.!?]+[\"')\]]?)(\s+)")
_SPOKEN = re.compile(r"\w")  # a lone "." would be read aloud as "dot"


_CLAUSE = re.compile(r"([,;:]|\s[\u2014\u2013-]+)\s")  # a pause inside a sentence; "1,200" has no space after its comma


class SentenceSplitter:
    """Cuts a streamed reply into the pieces that are spoken one at a time. `first_clause_words`: the very first piece may end at a
    comma, colon or dash once it has at least that many words, so speech starts sooner when the first sentence is long
    (0: only at sentence ends, as before). A pause inside brackets or quotes is never a place to stop."""

    def __init__(self, first_clause_words: int = 0) -> None:
        self._buf = ""
        self._clause_words = first_clause_words
        self._emitted = False

    def _first_clause(self, before: int) -> int | None:
        """Where the first piece may end (an index into the buffer), or None. `before`: the first sentence end, if any."""
        for m in _CLAUSE.finditer(self._buf):
            if m.end() > before:
                return None
            head = self._buf[: m.start(1)]
            if len(head.split()) < self._clause_words:
                continue
            if head.count("(") != head.count(")") or head.count('"') % 2 or head.count("\u201c") != head.count("\u201d"):
                continue
            return m.end(1)
        return None

    def feed(self, chunk: str) -> list[str]:
        self._buf += chunk
        out: list[str] = []
        while True:
            m = _END.search(self._buf)
            if self._clause_words and not self._emitted:
                cut = self._first_clause(m.start(1) if m else len(self._buf) + 1)
                if cut is not None:
                    piece, self._buf, self._emitted = self._buf[:cut].strip(), self._buf[cut:], True
                    if piece and _SPOKEN.search(piece):
                        out.append(piece)
                    continue
            if not m:
                break
            sentence = self._buf[: m.end(1)].strip()
            self._buf = self._buf[m.end():]
            if sentence and _SPOKEN.search(sentence):
                out.append(sentence)
                self._emitted = True
        return out

    def flush(self) -> str | None:
        rest, self._buf = self._buf.strip(), ""
        return rest if rest and _SPOKEN.search(rest) else None


_WORDS = re.compile(r"[a-z0-9']+")
# words that carry no request ("I said ...", "could you ... please") and so never decide whether two requests match
_FILLER = frozenset("i i'm said say the a an to it please could would can will you me my just um uh hey ok okay so "
                    "now on in at of for with".split())
_CORRECTION = frozenset("no not don't dont actually wait instead stop cancel never nope".split())


def _words(text: str, wake_words) -> list[str]:
    return [w for w in _WORDS.findall((text or "").lower()) if w not in wake_words]


def word_overlap(a: str, b: str, wake_words=()) -> float:
    """Share of the shorter request's content words that the other one also has (0..1)."""
    wa = {w for w in _words(a, wake_words) if w not in _FILLER}
    wb = {w for w in _words(b, wake_words) if w not in _FILLER}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def is_repeat(in_flight: str, new: str, wake_words=(), threshold: float = 0.7) -> bool:
    """True when `new` asks for the same thing as `in_flight`; a correction ("no, ...", "stop") never is."""
    words = _words(new, wake_words)
    if not words or words[0] in _CORRECTION or {"stop", "cancel"} & set(words):
        return False
    return word_overlap(in_flight, new, wake_words) >= threshold
