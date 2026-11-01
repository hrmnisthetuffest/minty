"""The vault: files are the truth, one git commit per write, nothing is ever deleted.

Content that came from outside (web, mail, forwarded messages, files) can only land in inbox/ or sources/ and is
marked untrusted; moving it anywhere else needs the user's approval. Health and finance records are not notes.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.vault.index import Index
from minty_core.vault.note import CONFIDENCE, Note, dump, parse

FOLDERS = ("inbox", "daily", "projects", "areas", "notes", "sources", "people", "decisions")
UNTRUSTED_FOLDERS = ("inbox", "sources")
TRUSTED_ORIGINS = {"user": "verified", "agent": "inferred"}  # anything else is outside content
ARCHIVE = "_archive"
_SLUG = re.compile(r"[^a-z0-9]+")


class VaultError(ValueError):
    """A request the vault refuses; the message is safe to say aloud."""


def slug(text: str, limit: int = 40) -> str:
    ascii_ = text.lower().encode("ascii", "ignore").decode()
    return _SLUG.sub("-", ascii_).strip("-")[:limit].strip("-") or "note"


class Vault:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time, git: bool = True):
        self.root = Path(root).expanduser()
        self._clock = clock
        self._lock = threading.RLock()
        for name in (*FOLDERS, ARCHIVE, "_minty"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.index = Index(self.root / "_minty" / "index.db")
        self.git_ok = False
        if git:
            self._init_git()
        self.refresh()

    # ---- git -----------------------------------------------------------------------------------------
    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Minty", "-c", "user.email=minty@localhost",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")

    def _init_git(self) -> None:
        if shutil.which("git") is None:
            return
        try:
            if not (self.root / ".git").exists():
                self._git("init", "-q")
            self.git_ok = True
            ignore = self.root / ".gitignore"
            if not ignore.exists():
                ignore.write_text("_minty/index.db*\n_minty/changes.log\n", encoding="utf-8")
                self._commit("start the notes repository")
        except (OSError, subprocess.SubprocessError):
            self.git_ok = False

    def _commit(self, message: str) -> None:
        if not self.git_ok:
            log = self.root / "_minty" / "changes.log"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.fromtimestamp(self._clock()).isoformat(timespec='seconds')} {message}\n")
            return
        try:
            self._git("add", "-A")
            self._git("commit", "-q", "-m", message)
        except (OSError, subprocess.SubprocessError):
            self.git_ok = False
            self._commit(message)

    # ---- time and ids --------------------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds")

    def _new_id(self) -> str:
        day = self._now()[:10]
        while True:
            ident = f"{day}-{secrets.token_hex(2)}"
            if self.index.row(ident) is None:
                return ident

    # ---- reading -------------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Pick up files added, edited or removed outside Minty (and adopt files without a header)."""
        with self._lock:
            known = self.index.mtimes()
            seen: set[str] = set()
            adopted = False
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d not in (".git", "_minty")]
                for name in filenames:
                    if not name.endswith(".md"):
                        continue
                    full = Path(dirpath) / name
                    rel = full.relative_to(self.root).as_posix()
                    seen.add(rel)
                    mtime = full.stat().st_mtime
                    if known.get(rel) == mtime:
                        continue
                    try:
                        note = parse(full.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
                    note.path = rel
                    if not note.id or self._id_used_elsewhere(note.id, rel):
                        note = self._adopt(note, full)
                        mtime = full.stat().st_mtime
                        adopted = True
                    self.index.upsert(note, mtime)
            for gone in set(known) - seen:
                self.index.drop_path(gone)
            if adopted:
                self._commit("adopt files added outside Minty")

    def _id_used_elsewhere(self, ident: str, rel: str) -> bool:
        row = self.index.row(ident)
        return row is not None and row["path"] != rel and (self.root / row["path"]).exists()

    def _adopt(self, note: Note, full: Path) -> Note:
        stem = full.stem
        note.id = self._new_id()
        note.title = note.title or stem
        note.created = note.created or self._now()
        note.updated = self._now()
        note.source = note.source or "file"
        note.confidence = "untrusted"
        atomic_write_text(full, dump(note))
        return note

    def get(self, ident: str) -> Note | None:
        row = self.index.row(ident)
        if row is None:
            return None
        try:
            note = parse((self.root / row["path"]).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        note.path = row["path"]
        note.id = note.id or ident
        return note

    def search_ids(self, query: str, limit: int = 10, include_archived: bool = False) -> list[str]:
        self.refresh()
        return [i for i, _ in self.index.search(query, limit, include_archived)]

    def list(self, folder: str | None = None, limit: int = 200) -> list[Note]:
        self.refresh()
        return [n for n in (self.get(r["id"]) for r in self.index.rows(folder, limit=limit)) if n]

    def recent(self, n: int = 10) -> list[Note]:
        return self.list(None, limit=n)

    def backlinks(self, ident: str) -> list[Note]:
        return [n for n in (self.get(i) for i in self.index.backlinks(ident)) if n]

    def vocabulary(self):
        self.refresh()
        return self.index.vocabulary()

    def broken_links(self) -> list[tuple[str, str]]:
        self.refresh()
        return self.index.broken_links()

    def status(self) -> dict:
        return {"root": str(self.root), "notes": self.index.count(), "inbox": len(self.index.rows("inbox")),
                "versioning": "git" if self.git_ok else "no git installed: changes are logged in _minty/changes.log"}

    # ---- writing -------------------------------------------------------------------------------------
    def _write(self, note: Note) -> None:
        full = self.root / note.path
        atomic_write_text(full, dump(note))
        self.index.upsert(note, full.stat().st_mtime)

    def create(self, title: str, body: str = "", *, folder: str = "inbox", origin: str = "user", source: str = "",
               confidence: str | None = None, tags=(), project: str = "", note_id: str | None = None,
               filename: str | None = None) -> Note:
        title = " ".join(str(title).split())[:120]
        if not title:
            raise VaultError("A note needs a title.")
        if folder not in FOLDERS:
            raise VaultError(f"There is no {folder!r} folder in the vault.")
        outside = origin not in TRUSTED_ORIGINS
        if outside:
            confidence = "untrusted"
            if folder not in UNTRUSTED_FOLDERS:
                folder = "inbox"
        else:
            confidence = confidence if confidence in CONFIDENCE and confidence != "untrusted" \
                else TRUSTED_ORIGINS[origin]
        with self._lock:
            ident = note_id or self._new_id()
            stem = Path(filename).name if filename else f"{slug(title)}-{ident[-4:]}"
            rel = f"{folder}/{stem}.md"
            while (self.root / rel).exists():
                rel = f"{folder}/{stem}-{secrets.token_hex(1)}.md"
            now = self._now()
            note = Note(id=ident, title=title, body=str(body), created=now, updated=now,
                        source=source or origin, confidence=confidence, tags=list(tags), project=project, path=rel)
            self._write(note)
            self._commit(f"create: {title} ({ident})")
            return note

    def update(self, ident: str, *, body: str | None = None, title: str | None = None, tags=None,
               project: str | None = None, origin: str = "user") -> Note:
        with self._lock:
            note = self._editable(ident, origin)
            if body is not None:
                note.body = str(body)
            if title is not None and " ".join(title.split()):
                note.title = " ".join(title.split())[:120]
            if tags is not None:
                note.tags = list(tags)
            if project is not None:
                note.project = project
            note.updated = self._now()
            self._write(note)
            self._commit(f"edit: {note.title} ({ident})")
            return note

    def append(self, ident: str, text: str, *, origin: str = "user") -> Note:
        with self._lock:
            note = self._editable(ident, origin)
            note.body = note.body.rstrip("\n") + ("\n\n" if note.body.strip() else "") + str(text).strip() + "\n"
            note.updated = self._now()
            self._write(note)
            self._commit(f"append: {note.title} ({ident})")
            return note

    def _editable(self, ident: str, origin: str) -> Note:
        note = self.get(ident)
        if note is None:
            raise VaultError("No note with that id.")
        if note.path.startswith(ARCHIVE + "/"):
            raise VaultError("That note is archived; restore it first.")
        if origin not in TRUSTED_ORIGINS and note.folder not in UNTRUSTED_FOLDERS:
            raise VaultError("Content from outside can only be added to the inbox or sources.")
        return note

    def _relocate(self, note: Note, old: Path) -> None:
        """The note is written at its new path first and the old file is removed only after, so a crash between
        the two leaves two copies (the adoption pass gives the second a new id), never none."""
        self._write(note)
        if old != self.root / note.path:
            old.unlink()

    def move(self, ident: str, folder: str, *, approved: bool = False) -> Note:
        if folder not in FOLDERS:
            raise VaultError(f"There is no {folder!r} folder in the vault.")
        with self._lock:
            note = self.get(ident)
            if note is None:
                raise VaultError("No note with that id.")
            leaving_untrusted = note.confidence == "untrusted" and folder not in UNTRUSTED_FOLDERS
            if leaving_untrusted and not approved:
                raise VaultError("That note came from outside; moving it out of the inbox needs your approval.")
            old = self.root / note.path
            rel = f"{folder}/{Path(note.path).name}"
            while (self.root / rel).exists() and (self.root / rel) != old:
                rel = f"{folder}/{Path(note.path).stem}-{secrets.token_hex(1)}.md"
            if leaving_untrusted:
                note.confidence = "inferred"  # you approved it landing here; that is not the same as verified
            note.path = rel
            note.updated = self._now()
            self._relocate(note, old)
            self._commit(f"move: {note.title} -> {folder} ({ident})")
            return note

    def erase(self, ident: str) -> bool:
        """Remove a note for good (file, index entry, one commit). Reachable only through forget-with-proof, which
        is a class-3 question; nothing else calls it. The git history still holds earlier versions of the file."""
        with self._lock:
            note = self.get(ident)
            if note is None:
                return False
            (self.root / note.path).unlink(missing_ok=True)
            self.index.drop_path(note.path)
            self._commit(f"erase: {ident}")
            return True

    def archive(self, ident: str) -> Note:
        with self._lock:
            note = self.get(ident)
            if note is None:
                raise VaultError("No note with that id.")
            if note.path.startswith(ARCHIVE + "/"):
                return note
            old = self.root / note.path
            rel = f"{ARCHIVE}/{note.path}"
            while (self.root / rel).exists():
                rel = f"{ARCHIVE}/{Path(note.path).parent}/{Path(note.path).stem}-{secrets.token_hex(1)}.md"
            note.path = rel
            note.updated = self._now()
            self._relocate(note, old)
            self._commit(f"archive: {note.title} ({ident})")
            return note
