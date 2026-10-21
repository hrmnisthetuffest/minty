"""Quarantine for downloads (571 751). A file the browser saved is moved out of reach of the file tools into an owner-only
folder, hashed, and (with your free VirusTotal key) looked up BY HASH ONLY: the file itself is never uploaded. It is released
to your Downloads folder only when you approve it, never when engines flag it, and never if it is a program file.
Nothing here opens or runs a file."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from minty_core.web.db import WebDB

PROGRAM_SUFFIXES = {".exe", ".msi", ".bat", ".cmd", ".sh", ".scr", ".jar", ".appimage", ".deb", ".rpm", ".apk", ".dmg",
                    ".com", ".ps1", ".vbs", ".run"}
VT_FILES = "https://www.virustotal.com/api/v3/files/"
PER_MINUTE, PER_DAY = 4, 500


def clean_name(name: str) -> str:
    base = Path(str(name)).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:80].strip(". ")
    return base or "download"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class VirusTotal:
    def __init__(self, key: Callable[[], str | None], get: Callable[[str, dict], Awaitable[tuple[int, dict]]],
                 db: WebDB, clock: Callable[[], float] = time.time):
        self._key, self._get, self._db, self._clock = key, get, db, clock
        self._recent: deque[float] = deque()

    def _day_count(self) -> int:
        today = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        day, _, n = self._db.meta("vt_day").partition(":")
        return int(n or 0) if day == today else 0

    async def lookup(self, sha256: str) -> dict:
        key = self._key()
        if not key:
            return {"state": "nokey", "message": "No VirusTotal key is set, so I only hashed it. Add a free one with: "
                                                 "minty set-key plugin.web.virustotal_key"}
        now = self._clock()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if len(self._recent) >= PER_MINUTE:
            return {"state": "skipped", "message": f"VirusTotal allows {PER_MINUTE} lookups a minute and that is used "
                                                   "up; try again in a minute."}
        if self._day_count() >= PER_DAY:
            return {"state": "skipped", "message": f"VirusTotal's free {PER_DAY} lookups for today are used up; "
                                                   "try tomorrow."}
        self._recent.append(now)
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        self._db.set_meta("vt_day", f"{today}:{self._day_count() + 1}")
        try:
            status, body = await self._get(VT_FILES + sha256, {"x-apikey": key})
        except Exception:  # noqa: BLE001 — network trouble is reported, never raised
            return {"state": "error", "message": "VirusTotal could not be reached."}
        if status == 404:
            return {"state": "unknown", "message": "VirusTotal has never seen this file. That is normal for a new or "
                                                    "private file and is not proof that it is safe."}
        if status in (401, 403):
            return {"state": "error", "message": "VirusTotal rejected the key."}
        if status != 200:
            return {"state": "error", "message": f"VirusTotal could not be reached (HTTP {status})."}
        st = (((body or {}).get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {}
        mal, sus = int(st.get("malicious", 0)), int(st.get("suspicious", 0))
        total = sum(int(v) for v in st.values())
        if mal:
            text = f"VirusTotal: {mal} of {total} engines flag it as malicious."
        elif sus:
            text = f"VirusTotal: {sus} of {total} engines call it suspicious; none say malicious. Be careful."
        else:
            text = f"VirusTotal: none of {total} engines flag it."
        return {"state": "known", "message": text, "malicious": mal, "suspicious": sus, "total": total}


class Quarantine:
    def __init__(self, db: WebDB, folder: Path, vt: VirusTotal | None = None, clock: Callable[[], float] = time.time):
        self.db, self.folder, self.vt, self._clock = db, Path(folder), vt, clock

    def get(self, ident: int) -> dict | None:
        with self.db.lock:
            row = self.db.db.execute("SELECT * FROM downloads WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def list(self, status: str = "quarantined") -> list[dict]:
        with self.db.lock:
            return [dict(r) for r in self.db.db.execute(
                "SELECT * FROM downloads WHERE status=? ORDER BY id", (status,)).fetchall()]

    def ingest(self, path: Path, source: str = "", name: str = "") -> dict:
        """Move a saved file into quarantine. `name` is the name the site suggested (never trusted as a path)."""
        path = Path(path)
        name = clean_name(name or path.name)
        digest, size = sha256_of(path), path.stat().st_size
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO downloads(name, sha256, size, source, path, created) "
                                     "VALUES (?,?,?,?,?,?)", (name, digest, size, str(source)[:300], "", self._clock()))
            ident = cur.lastrowid
            target = self.folder / f"{ident}-{name}"
            shutil.move(str(path), target)
            os.chmod(target, 0o600)
            self.db.db.execute("UPDATE downloads SET path=? WHERE id=?", (str(target), ident))
            self.db.db.commit()
        return self.get(ident)

    async def lookup(self, ident: int) -> dict:
        row = self.get(ident)
        if row is None:
            return {"state": "error", "message": "I have no such download."}
        if row["vt"]:
            return json.loads(row["vt"])
        if self.vt is None:
            return {"state": "nokey", "message": "Lookups are not set up; the file is hashed only."}
        result = await self.vt.lookup(row["sha256"])
        if result["state"] in ("known", "unknown"):
            with self.db.lock:
                self.db.db.execute("UPDATE downloads SET vt=? WHERE id=?", (json.dumps(result), ident))
                self.db.db.commit()
        return result

    def release(self, ident: int, dest_folder: Path) -> Path:
        row = self.get(ident)
        if row is None:
            raise ValueError("I have no such download.")
        if row["status"] != "quarantined":
            raise ValueError(f"That one is already {row['status']}.")
        if Path(row["name"]).suffix.lower() in PROGRAM_SUFFIXES:
            raise ValueError("That is a program file; program files are never released from quarantine. Delete it.")
        verdict = json.loads(row["vt"]) if row["vt"] else {}
        if verdict.get("malicious"):
            raise ValueError(f"{verdict['malicious']} security engines flag it as malicious, so I will not release it. "
                             "Delete it instead.")
        dest_folder = Path(dest_folder).expanduser()
        dest_folder.mkdir(parents=True, exist_ok=True)
        target = dest_folder / row["name"]
        n = 1
        while target.exists():
            target = dest_folder / f"{Path(row['name']).stem} ({n}){Path(row['name']).suffix}"
            n += 1
        shutil.move(row["path"], target)
        os.chmod(target, 0o644)
        with self.db.lock:
            self.db.db.execute("UPDATE downloads SET status='released', path=? WHERE id=?", (str(target), ident))
            self.db.db.commit()
        return target

    def discard(self, ident: int) -> bool:
        row = self.get(ident)
        if row is None or row["status"] != "quarantined":
            return False
        Path(row["path"]).unlink(missing_ok=True)
        with self.db.lock:
            self.db.db.execute("UPDATE downloads SET status='deleted' WHERE id=?", (ident,))
            self.db.db.commit()
        return True
