"""One note on disk: a small header, then Markdown. Parsed and written by hand, so no YAML dependency and no
header value can add another field (every value is one line)."""
from __future__ import annotations

from dataclasses import dataclass, field

CONFIDENCE = ("verified", "inferred", "untrusted")
_FIELDS = ("id", "title", "created", "updated", "source", "confidence", "tags", "project")


def _line(value) -> str:
    return " ".join(str(value or "").split())


@dataclass
class Note:
    id: str = ""
    title: str = ""
    body: str = ""
    created: str = ""
    updated: str = ""
    source: str = ""
    confidence: str = "inferred"
    tags: list[str] = field(default_factory=list)
    project: str = ""
    path: str = ""  # relative to the vault, set by the store; never written into the file

    @property
    def folder(self) -> str:
        return self.path.split("/", 1)[0] if "/" in self.path else ""


def dump(note: Note) -> str:
    confidence = note.confidence if note.confidence in CONFIDENCE else "untrusted"
    tags = ", ".join(t for t in (_line(t).replace(",", " ") for t in note.tags) if t)
    values = {"id": _line(note.id), "title": _line(note.title), "created": _line(note.created),
              "updated": _line(note.updated), "source": _line(note.source), "confidence": confidence,
              "tags": tags, "project": _line(note.project)}
    head = "".join(f"{k}: {values[k]}\n" for k in _FIELDS)
    body = note.body if note.body.endswith("\n") or not note.body else note.body + "\n"
    return f"---\n{head}---\n{body}"


def parse(text: str) -> Note:
    """A note from file text. No header: everything is body and the id is empty (the store adopts it)."""
    if not text.startswith("---\n"):
        return Note(body=text)
    lines = text.split("\n")
    values: dict[str, str] = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() in _FIELDS:
            values[key.strip()] = value.strip()
    if end is None:  # an unclosed header is not a header
        return Note(body=text)
    confidence = values.get("confidence", "inferred")
    return Note(id=values.get("id", ""), title=values.get("title", ""), body="\n".join(lines[end + 1:]),
                created=values.get("created", ""), updated=values.get("updated", ""),
                source=values.get("source", ""),
                confidence=confidence if confidence in CONFIDENCE else "untrusted",
                tags=[t.strip() for t in values.get("tags", "").split(",") if t.strip()],
                project=values.get("project", ""))
