"""The asset library: every file the studio uses, and where it came from.

A file is only kept with a licence from a closed list. Whether a video may go out by itself depends on this record: a post made from
anything not on it is never automatic. A rendered video records the files it was made from, so one made only from licensed files is
clean and one made from anything else is not. Credits that a licence requires are collected for the caption.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from minty_core.studio.sniff import AUDIO_TYPES, IMAGE_TYPES, VIDEO_TYPES, sniff

LICENSES = {  # name -> (what it means, needs a credit)
    "owned": ("made or recorded by you", False),
    "generated": ("made here by Minty (a voice or a plain music bed): no third party", False),
    "cc0": ("public domain (Creative Commons Zero)", False),
    "pexels": ("the Pexels licence: free to use, a credit to the creator and Pexels is asked for", True),
    "pixabay": ("the Pixabay content licence: free to use", False),
    "nasa": ("NASA media: generally not copyrighted in the US; credit NASA and never imply it endorses anything", True),
    "cc-by": ("Creative Commons Attribution: free to use with the credit the creator asks for", True),
    "permission": ("the owner gave you permission (say who and when in the source)", False),
}
KINDS = ("video", "gameplay", "music", "image", "voice", "render")


class AssetError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    id: int
    path: str
    kind: str
    license: str
    source: str
    attribution: str
    note: str
    sha: str
    added: float
    parents: tuple[int, ...] = ()


_COLS = "id, path, kind, license, source, attribution, note, sha, added, parents"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class AssetLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.root / "assets.sqlite", check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS assets (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, kind TEXT NOT NULL,"
            " license TEXT NOT NULL, source TEXT NOT NULL, attribution TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',"
            " sha TEXT NOT NULL UNIQUE, added REAL NOT NULL, parents TEXT NOT NULL DEFAULT '[]')")
        self._db.commit()

    def shared(self):
        """The database connection and its lock, for the catalogue that keeps its tables beside the assets."""
        return self._db, self._lock

    @staticmethod
    def _asset(r) -> Asset:
        return Asset(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], tuple(json.loads(r[9] or "[]")))

    def _by_sha(self, sha: str) -> Asset | None:
        row = self._db.execute(f"SELECT {_COLS} FROM assets WHERE sha=?", (sha,)).fetchone()
        return self._asset(row) if row else None

    def get(self, ident: int) -> Asset | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_COLS} FROM assets WHERE id=?", (int(ident),)).fetchone()
        return self._asset(row) if row else None

    def find(self, path: Path) -> Asset | None:
        """The record for a file, by its content (a copy or a rename is still the same file)."""
        try:
            sha = _sha(Path(path))
        except OSError:
            return None
        with self._lock:
            return self._by_sha(sha)

    def add(self, src: Path, kind: str, license: str | None, *, source: str, attribution: str = "", note: str = "") -> Asset:
        if kind not in KINDS:
            raise AssetError(f"kind must be one of: {', '.join(KINDS)}")
        if license not in LICENSES:
            options = "; ".join(f"{k}: {v[0]}" for k, v in LICENSES.items())
            raise AssetError(f"say what licence this file is under, one of: {options}")
        if LICENSES[license][1] and not attribution.strip():
            raise AssetError(f"the {license} licence needs a credit: say who to credit")
        if not source.strip():
            raise AssetError("say where it came from")
        src = Path(src)
        if not src.is_file():
            raise AssetError(f"{src.name} is not there")
        ext = sniff(src)  # decided by the bytes, never by the name
        if ext is None:
            raise AssetError(f"{src.name} is not a video, music or picture file I accept (it looks like text, a playlist or a document)")
        if kind == "image" and ext not in IMAGE_TYPES:
            raise AssetError(f"{src.name} is not a picture")
        if kind in ("video", "gameplay", "render") and ext not in VIDEO_TYPES:
            raise AssetError(f"{src.name} is {'a picture' if ext in IMAGE_TYPES else 'audio'}, not footage")
        if kind in ("music", "voice") and ext not in AUDIO_TYPES | VIDEO_TYPES:
            raise AssetError(f"{src.name} is not music or a voice recording (it is a picture)")
        sha = _sha(src)
        with self._lock:
            known = self._by_sha(sha)
            if known:
                return known
            dest_dir = self.root / kind
            dest_dir.mkdir(parents=True, exist_ok=True)
            stem = re.sub(r"[^A-Za-z0-9._-]+", "-", src.stem).strip("-.")[:60] or "file"
            dest = dest_dir / f"{sha[:10]}-{stem}{ext}"  # a name from the bytes, not the sender: never .m3u8, never spaces
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
            cur = self._db.execute(
                "INSERT INTO assets(path, kind, license, source, attribution, note, sha, added) VALUES (?,?,?,?,?,?,?,?)",
                (str(dest), kind, license, source.strip()[:300], attribution.strip()[:300], note.strip()[:200], sha, time.time()))
            self._db.commit()
            return self.get(cur.lastrowid)

    def register_render(self, path: Path, parents: list[Path]) -> Asset:
        """A made video, with the files it was made from. Its licence is the sum of theirs."""
        path = Path(path)
        sha = _sha(path)
        parent_ids = [a.id for a in (self.find(p) for p in parents) if a is not None]
        with self._lock:
            known = self._by_sha(sha)
            if known:
                return known
            dest_dir = self.root / "render"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{sha[:10]}-{path.name}"
            if path.resolve() != dest.resolve():
                shutil.copy2(path, dest)
            known_parents = len(parent_ids) == len(parents) and bool(parents)
            cur = self._db.execute(
                "INSERT INTO assets(path, kind, license, source, attribution, note, sha, added, parents) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(dest), "render", "owned" if known_parents else "unknown", "made here from " + (", ".join(Path(p).name for p in parents) or "nothing recorded"),
                 "", "", sha, time.time(), json.dumps(parent_ids if known_parents else [-1])))
            self._db.commit()
            return self.get(cur.lastrowid)

    def register_agent_render(self, path: Path) -> Asset:
        """A video or picture the user's coding agent made in the studio folder: the user's own, with nothing else recorded behind it."""
        path = Path(path)
        sha = _sha(path)
        with self._lock:
            known = self._by_sha(sha)
            if known:
                return known
            dest_dir = self.root / "render"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{sha[:10]}-{path.name}"
            shutil.copy2(path, dest)
            cur = self._db.execute(
                "INSERT INTO assets(path, kind, license, source, attribution, note, sha, added) VALUES (?,?,?,?,?,?,?,?)",
                (str(dest), "render", "owned", "made by the coding agent in the studio folder", "", "", sha, time.time()))
            self._db.commit()
            return self.get(cur.lastrowid)

    def list(self, kind: str | None = None) -> list[Asset]:
        with self._lock:
            sql = f"SELECT {_COLS} FROM assets" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC"
            return [self._asset(r) for r in self._db.execute(sql, (kind,) if kind else ())]

    def pick(self, kind: str, rng: random.Random | None = None) -> Asset | None:
        found = self.list(kind)
        return (rng or random).choice(found) if found else None

    def _clean(self, asset: Asset, seen: frozenset = frozenset()) -> bool:
        if asset.license == "unknown" or -1 in asset.parents or asset.id in seen:
            return False
        return all((p := self.get(i)) is not None and self._clean(p, seen | {asset.id}) for i in asset.parents)

    def problems_for_posting(self, files: list[Path]) -> list[str]:
        """Why these files cannot go out automatically: anything without a licence on record, or made from such files."""
        out = []
        for f in files:
            asset = self.find(Path(f))
            if asset is None:
                out.append(f"{Path(f).name} is not in the asset library, so its licence is unknown")
            elif not self._clean(asset):
                out.append(f"{Path(f).name} was made from a file whose licence is unknown")
        return out

    def credits(self, files: list[Path]) -> list[str]:
        """The credit lines the licences of these files (and of what they were made from) ask for, once each."""
        lines: list[str] = []

        def walk(asset: Asset | None, seen: set) -> None:
            if asset is None or asset.id in seen:
                return
            seen.add(asset.id)
            if LICENSES.get(asset.license, ("", False))[1] and asset.attribution and asset.attribution not in lines:
                lines.append(asset.attribution)
            for pid in asset.parents:
                walk(self.get(pid) if pid > 0 else None, seen)

        for f in files:
            walk(self.find(Path(f)), set())
        return lines
