"""Capture: drop anything in, and it comes out as sourced, linked knowledge, or waits in the inbox for you.

An input (typed or dictated text, a link, a file) is first kept as an **original** (a source) and as a **capture** in the inbox. Processing then reads it, splits a brain dump into
single thoughts, guesses what kind each is, finds the people and organisations the world model already knows (a name it does not know is only *proposed*), notices when it is
the same thought again (the repeat becomes a second piece of evidence for the first, not a second copy), proposes links to similar knowledge (suggested, never confirmed), writes
an extractive summary beside long text (marked generated), and files it. All of it is deterministic: no model is needed, and nothing is invented that is not in the text.

What may be filed by itself: things *you* put in. What waits for your approval: anything captured in a turn that read outside content, anything a page or file wrote that looks
like orders to a model, and anything Minty wrote on its own (a lesson from a finished mission). Links come from `fetch` (the safe page reader), `pdf_text` and `ocr` (the same
readers the Telegram intake uses), so there is no new way to touch the network or a file. A failure keeps the input, says what went wrong, and can be retried.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.knowledge.reason import Reasoner, stem
from minty_core.knowledge.store import KnowledgeError, KnowledgeStore
from minty_core.provenance import looks_like_injection
from minty_core.vault.search import significant

MAX_ATOMS = 12
SUMMARY_FROM = 600
SUMMARY_CHARS = 420
EXCERPT = 4000
_LABEL = re.compile(r"^\s*(idea|lesson learned|lesson|note|question|observation|insight|hypothesis|opportunity|reference|todo|thought)\s*[:\-–—]\s*", re.I)
_LABEL_KIND = {"idea": "idea", "lesson learned": "lesson", "lesson": "lesson", "note": "note", "question": "question", "observation": "observation", "insight": "insight",
               "hypothesis": "hypothesis", "opportunity": "opportunity", "reference": "reference", "todo": "note", "thought": "note"}
_CUES = (("lesson", re.compile(r"\b(lesson|learned|learnt|next time|never again|takeaway|mistake was)\b", re.I)),
         ("hypothesis", re.compile(r"\b(i think|i guess|i suspect|probably|maybe|might be|perhaps|hypothes[ie]s|my guess|presumably)\b", re.I)),
         ("idea", re.compile(r"\b(what if|we could|could build|how about|imagine|let'?s try|should build|brainstorm)\b", re.I)),
         ("opportunity", re.compile(r"\b(opportunity|market gap|untapped|no one is doing|nobody is doing|demand for)\b", re.I)),
         ("observation", re.compile(r"\b(noticed|observed|saw that|seems to|turns out|it looks like)\b", re.I)),
         ("concept", re.compile(r"\b(is defined as|refers to|is a (?:type|kind|form) of)\b", re.I)))
_DECISION = re.compile(r"\b(decided|we will go with|i'?ll go with|let'?s go with|final decision|we are going with)\b", re.I)
_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")
_COMMON_CAPS = {"I", "I'm", "I'll", "I've", "I'd", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December", "The", "A", "An", "And", "But", "Or", "So", "If", "It", "This", "That", "We", "You", "They", "He", "She", "Not", "Also", "Then"}


def summarize(text: str, max_chars: int = SUMMARY_CHARS) -> str:
    """Extractive: the sentences that carry the most of the text's own subject words, in their original order. Nothing is rewritten, so nothing can be made up."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 25]
    if not sentences:
        return ""
    freq = Counter(stem(w) for s in sentences for w in significant(s))
    scored = sorted(range(len(sentences)), key=lambda i: (-sum(freq[stem(w)] for w in significant(sentences[i])) / (len(significant(sentences[i])) ** 0.5 or 1), i))
    chosen, used = [], 0
    for i in scored:
        if used + len(sentences[i]) + 1 > max_chars:
            continue
        chosen.append(i)
        used += len(sentences[i]) + 1
        if len(chosen) == 3:
            break
    if not chosen:
        return sentences[0][: max_chars - 1].rstrip() + "…"
    return " ".join(sentences[i] for i in sorted(chosen))


def guess_kind(text: str, hint: str = "") -> tuple[str, str]:
    """(kind, text without its leading label). A label the writer typed ("Idea: ...") wins, then a hint from the caller, then a question mark, then the wording."""
    m = _LABEL.match(text)
    if m:
        return _LABEL_KIND[m.group(1).lower()], text[m.end():].strip()
    if hint:
        return hint, text
    if text.rstrip().endswith("?"):
        return "question", text
    for kind, rx in _CUES:
        if rx.search(text):
            return kind, text
    return "note", text


def title_of(text: str, limit: int = 90) -> str:
    first = re.split(r"(?<=[.!?])\s+|\n", text.strip(), maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    cut = first[: limit - 1].rsplit(" ", 1)[0] or first[: limit - 1]
    return cut.rstrip(",;:- ") + "…"


def entity_candidates(text: str) -> list[str]:
    """Capitalised names in the middle of sentences: only ever proposed, since the world model is the owner's to grow."""
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        tokens = sentence.split()
        run: list[str] = []
        for tok in [*tokens[1:], ""]:
            word = re.sub(r"^[^\w]+|[^\w'&-]+$", "", tok)
            if word[:1].isupper() and len(word) >= 3 and word not in _COMMON_CAPS:
                run.append(word)
                continue
            if run:
                name = " ".join(run[:3])
                if name not in found:
                    found.append(name)
            run = []
    return found


class Capturer:
    def __init__(self, store: KnowledgeStore, reasoner: Reasoner, world=None, fetch: Callable[[str], Awaitable[dict]] | None = None,
                 pdf_text: Callable[[bytes], str | None] | None = None, ocr: Callable[[Path], Awaitable[str | None]] | None = None):
        self.store, self.reasoner, self.world, self.fetch, self.pdf_text, self.ocr = store, reasoner, world, fetch, pdf_text, ocr

    # ---- taking things in -------------------------------------------------------------------------------------------
    async def capture(self, text: str = "", *, url: str = "", data: bytes | None = None, name: str = "", channel: str = "", kind_hint: str = "", origin: str = "you",
                      private: bool = False, process: bool = True, refs: list | None = None) -> dict:
        meta: dict = {"refs": [list(r) for r in refs]} if refs else {}
        if data is not None:
            kind = "pdf" if (name.lower().endswith(".pdf") or data[:4] == b"%PDF") else "image" if name.lower().endswith(_IMAGE) else "file"
            src = self.store.add_source(kind, "", name=name or "upload", data=data, origin="outside" if origin == "you" else origin, channel=channel, private=private, meta={"name": name, "bytes": len(data)})
            raw, meta = f"[file] {name or 'upload'}", {**meta, "source": src["id"], "name": name}
        elif url:
            raw, meta = url.strip(), {**meta, "url": url.strip()}
        else:
            raw = text
        cap = self.store.add_capture(raw, channel=channel, kind_hint=kind_hint, origin=origin, private=private, meta=meta)
        if data is not None:
            cap = self.store.update_capture(cap["id"], source_id=meta["source"])
        return await self.process(cap["id"]) if process else cap

    def add(self, kind: str, title: str, body: str = "", *, authored: str = "minty", summary: str = "", project: str = "", tags=(), private: bool = False, sources=(),
            actor: str = "minty", outside: bool = False) -> dict:
        """Something already analysed (by Minty, or by the owner through the panel) goes in the same way a captured thought does: the same thing again is more evidence for the first copy,
        names the world model knows are linked, and similar knowledge is proposed (never confirmed). Returns {item, duplicate, about, similar}."""
        text = f"{title} {body}".strip()
        dup = self.reasoner.find_duplicate(text, include_private=private, title=title, body=body)
        if dup is not None:
            for src in sources:
                sid, quote = (src, "") if isinstance(src, str) else (src[0], src[1])
                self.store.cite(dup["id"], sid, quote=quote or title[:200], actor=actor)
            return {"item": self.store.item(dup["id"]), "duplicate": True, "about": [], "similar": []}
        similar = self.reasoner.related(text, k=3, include_private=private)
        item = self.store.add_item(kind, title, body, authored=authored, summary=summary, project=project, tags=tags, private=private, sources=sources, actor=actor, outside=outside)
        about = []
        if self.world is not None:
            for e in self.world.recall(text, include_private=True)["entities"]:
                if re.search(r"\b" + re.escape(e["entity"]) + r"\b", text, re.I):
                    about.append(e["entity"])
                    self.store.link(f"item:{item['id']}", "about", f"world:{e['entity']}", why="named in the text", by="auto", outside=outside)
        for s in similar:
            self.store.link(f"item:{item['id']}", "relates to", s["ref"], why=s["why"], status="suggested", by="auto", score=s["score"])
        return {"item": item, "duplicate": False, "about": about, "similar": similar}

    async def process(self, cid: str) -> dict:
        cap = self.store.capture(cid)
        if cap is None:
            raise KnowledgeError("There is no such capture.")
        self.store.update_capture(cid, status="processing", attempts=cap["attempts"] + 1, error="")
        try:
            read = await self._read(cap)
            result = self._analyse(cap, read)
        except Exception as e:  # noqa: BLE001 — whatever went wrong is kept with the input, which stays retryable
            return self.store.update_capture(cid, status="failed", error=str(e)[:300] or e.__class__.__name__)
        flagged = bool(result["flags"])
        if cap["origin"] == "you" and not flagged:
            return self._file(cid, result)
        return self.store.update_capture(cid, status="review", result=result, source_id=result["source"])

    def approve(self, cid: str, actor: str = "you") -> dict:
        """File a capture that was waiting for you."""
        cap = self.store.capture(cid)
        if cap is None or cap["status"] not in ("review", "failed", "pending"):
            raise KnowledgeError("That capture is not waiting for approval.")
        if not cap["result"].get("atoms"):
            raise KnowledgeError("It has not been processed yet.")
        return self._file(cid, cap["result"], actor=actor)

    # ---- reading the input ------------------------------------------------------------------------------------------
    async def _read(self, cap: dict) -> dict:
        origin = cap["origin"]
        if cap["meta"].get("url"):
            if self.fetch is None:
                raise RuntimeError("opening links is not available here")
            page = await self.fetch(cap["meta"]["url"])
            if not page.get("ok"):
                raise RuntimeError(page.get("note") or "the page could not be opened")
            text = str(page.get("text") or "").strip()
            if not text:
                raise RuntimeError("the page had no readable text")
            src = self.store.add_source("link", text[:190_000], title=page.get("title") or cap["meta"]["url"], uri=cap["meta"]["url"], origin="outside", channel=cap["channel"],
                                        private=cap["private"], meta={"final_host": page.get("final_host", "")})
            return {"kind": "article", "title": page.get("title") or cap["meta"]["url"], "text": text, "source": src["id"], "content_origin": "outside"}
        if cap["meta"].get("source"):  # a file: its original was kept when it arrived
            src = self.store.source(cap["meta"]["source"])
            data = Path(src["path"]).read_bytes() if src and src["path"] else b""
            text = src["text"] if src else ""
            if not text.strip():
                if src["kind"] == "pdf" and self.pdf_text is not None:
                    text = self.pdf_text(data) or ""
                elif src["kind"] == "image" and self.ocr is not None:
                    text = await self.ocr(Path(src["path"])) or ""
                elif src["kind"] == "file":
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        text = ""
            if not text.strip():
                raise RuntimeError("no text could be read from it (the original is kept)")
            self.store.fill_source_text(src["id"], text[:190_000])
            return {"kind": "document", "title": src["title"], "text": text, "source": src["id"], "content_origin": "outside"}
        channel = cap["channel"]
        kind = "voice" if channel == "voice" else "task" if origin == "minty" else "chat" if channel == "chat" else "text"
        src = self.store.add_source(kind, cap["raw"], origin=origin, channel=channel, private=cap["private"])
        return {"kind": "text", "title": "", "text": cap["raw"], "source": src["id"], "content_origin": origin}

    # ---- understanding it -------------------------------------------------------------------------------------------
    def _atomise(self, text: str) -> list[str]:
        parts: list[str] = []
        for para in re.split(r"\n\s*\n", text.strip()):
            lines = [ln for ln in para.splitlines() if ln.strip()]
            if len(lines) > 1 and all(re.match(r"\s*(?:[-*•]|\d+[.)])\s+", ln) for ln in lines):
                parts += [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip() for ln in lines]
            elif para.strip():
                parts.append(" ".join(para.split()) if len(lines) > 1 and len(para) < 400 else para.strip())
        if len(parts) > MAX_ATOMS:
            parts = parts[: MAX_ATOMS - 1] + ["\n\n".join(parts[MAX_ATOMS - 1:])]
        return parts

    def _analyse(self, cap: dict, read: dict) -> dict:
        text, hint = read["text"], cap["kind_hint"]
        flags: list[str] = []
        if read["content_origin"] != "you" and looks_like_injection(text):
            flags.append("looks like instructions to an assistant: check it before filing")
        known = []
        if self.world is not None:
            known = [e["entity"] for e in self.world.recall(text, include_private=True)["entities"]]
        proposed = [n for n in entity_candidates(text) if n not in known and not any(n in k or k in n for k in known)]
        atoms = []
        pieces = [text] if read["kind"] != "text" else self._atomise(text)
        decisions = []
        for piece in pieces:
            if read["kind"] == "text":
                kind, body_text = guess_kind(piece, hint)
                title = title_of(body_text)
                body = "" if len(body_text) <= 140 and "\n" not in body_text else body_text
            else:
                kind, title = read["kind"], title_of(read["title"] or text, 140)
                body = text[:EXCERPT]
                body_text = text
            if _DECISION.search(body_text):
                decisions.append(title)
            mentioned = [k for k in known if re.search(r"\b" + re.escape(k) + r"\b", body_text, re.I)]
            dup = self.reasoner.find_duplicate(body_text, include_private=cap["private"], title=title, body=body)
            similar = [] if dup else self.reasoner.related(f"{title} {body_text}", k=3, include_private=cap["private"])
            atoms.append({"kind": kind, "title": title, "body": body, "summary": summarize(body_text) if len(body_text) > SUMMARY_FROM else "", "entities": mentioned,
                          "duplicate_of": dup["id"] if dup else "", "similar": similar, "item": ""})
        return {"atoms": atoms, "entities": {"known": known, "proposed": proposed[:8]}, "flags": flags, "source": read["source"], "content_origin": read["content_origin"],
                "decisions": decisions, "notes": ["This sounds like a decision: record it with world_decide so it keeps its reasoning."] if decisions else []}

    # ---- filing it --------------------------------------------------------------------------------------------------
    def _file(self, cid: str, result: dict, actor: str = "capture") -> dict:
        cap = self.store.capture(cid)
        outside = result["content_origin"] == "outside"
        authored = "outside" if outside else "minty" if cap["origin"] == "minty" else "you"
        sid = result["source"]
        for atom in result["atoms"]:
            if atom["duplicate_of"]:
                self.store.cite(atom["duplicate_of"], sid, quote=atom["title"][:200], actor=actor)
                atom["item"] = atom["duplicate_of"]
                continue
            tags = ["decision-candidate"] if atom["title"] in result.get("decisions", []) else []
            it = self.store.add_item(atom["kind"], atom["title"], atom["body"], authored=authored, summary=atom["summary"], private=cap["private"], sources=[(sid, atom["title"][:200])],
                                     attrs={"summary_by": "minty", "capture": cid} if atom["summary"] else {"capture": cid}, actor=actor, outside=outside, tags=tags)
            atom["item"] = it["id"]
            for rel, to in cap["meta"].get("refs", []):  # e.g. a finished mission this was drawn from
                self.store.link(f"item:{it['id']}", rel, to, why="where it came from", by="auto", outside=outside)
            for name in atom["entities"]:
                self.store.link(f"item:{it['id']}", "about", f"world:{name}", why="named in the text", by="auto", outside=outside)
            for s in atom["similar"]:
                self.store.link(f"item:{it['id']}", "relates to", s["ref"], why=s["why"], status="suggested", by="auto", score=s["score"])
        return self.store.update_capture(cid, status="filed", result=result, source_id=sid, error="", actor=actor)
