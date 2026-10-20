"""The encrypted private area (612): notes that are unreadable and absent from every index while it is locked.

* One random master key encrypts the notes (AES-256-GCM, the note's id bound in as associated data, so a file
  swapped for another is refused). The master key is stored only wrapped: once under a key derived (scrypt) from your
  passphrase, once under a recovery phrase that is shown a single time. Neither the passphrase nor the master key is
  ever written down.
* Unlocking holds the key in memory until you lock it or it has been idle for a while. The search index of private
  notes lives in memory only and is dropped on lock.
* Losing both the passphrase and the recovery phrase loses the notes. There is no back door.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.vault.index import words

MAGIC = b"MINTYPV1"
SCRYPT = dict(n=2**14, r=8, p=1)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
MAX_FAILS, FAIL_WINDOW_S, LOCKOUT_S = 5, 60.0, 60.0


class PrivateError(RuntimeError):
    pass


class Locked(PrivateError):
    pass


class WrongPassphrase(PrivateError):
    pass


def _crypto():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as e:
        raise PrivateError("the 'cryptography' package is not installed (pip install -e '.[backup]')") from e
    return AESGCM, Scrypt, InvalidTag


def _kdf(secret: str, salt: bytes) -> bytes:
    _a, Scrypt, _b = _crypto()
    return Scrypt(salt=salt, length=32, **SCRYPT).derive(secret.encode("utf-8"))


def _norm_recovery(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


class PrivateArea:
    def __init__(self, root: Path, clock: Callable[[], float] = time.time, auto_lock_s: float = 600.0):
        self.dir = Path(root) / "_private"
        self._clock, self._auto = clock, auto_lock_s
        self._key: bytes | None = None
        self._until = 0.0
        self._notes: dict[str, dict] = {}
        self._fails: list[float] = []

    # ---- state ---------------------------------------------------------------------------------------
    @property
    def _keys_path(self) -> Path:
        return self.dir / ".keys.json"

    def exists(self) -> bool:
        return self._keys_path.is_file()

    @property
    def unlocked(self) -> bool:
        if self._key is not None and self._clock() > self._until:
            self.lock()
        return self._key is not None

    def _need(self) -> bytes:
        if not self.unlocked:
            raise Locked("The private notes are locked. Unlock them at the terminal or in the panel.")
        self._until = self._clock() + self._auto  # use keeps it open
        return self._key  # type: ignore[return-value]

    def lock(self) -> None:
        self._key, self._notes = None, {}

    # ---- keys ----------------------------------------------------------------------------------------
    def _wrap(self, master: bytes, secret: str) -> dict:
        AESGCM, _s, _b = _crypto()
        salt, nonce = os.urandom(16), os.urandom(12)
        return {"salt": salt.hex(), "nonce": nonce.hex(),
                "wrapped": AESGCM(_kdf(secret, salt)).encrypt(nonce, master, MAGIC).hex()}

    def _unwrap(self, entry: dict, secret: str) -> bytes | None:
        AESGCM, _s, InvalidTag = _crypto()
        try:
            return AESGCM(_kdf(secret, bytes.fromhex(entry["salt"]))).decrypt(
                bytes.fromhex(entry["nonce"]), bytes.fromhex(entry["wrapped"]), MAGIC)
        except (InvalidTag, KeyError, ValueError):
            return None

    def create(self, passphrase: str, recovery: bool = True) -> str:
        """Make the area. Returns the recovery phrase (shown once, not stored), or '' when none was asked for."""
        if self.exists():
            raise PrivateError("The private area already exists.")
        if len(passphrase) < 8:
            raise PrivateError("Use a passphrase of at least 8 characters.")
        self.dir.mkdir(parents=True, exist_ok=True)
        master = os.urandom(32)
        phrase = "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(4)) if recovery else ""
        data = {"v": 1, "pass": self._wrap(master, passphrase.strip()),
                "recovery": self._wrap(master, _norm_recovery(phrase)) if phrase else None}
        atomic_write_text(self._keys_path, json.dumps(data), mode=0o600)
        return phrase

    def unlock(self, secret: str) -> None:
        if not self.exists():
            raise PrivateError("There is no private area yet.")
        now = self._clock()
        self._fails = [t for t in self._fails if now - t < FAIL_WINDOW_S]
        if len(self._fails) >= MAX_FAILS:
            raise PrivateError("Too many wrong tries; wait a minute and try again.")
        keys = json.loads(self._keys_path.read_text(encoding="utf-8"))
        master = self._unwrap(keys["pass"], secret.strip())
        if master is None and keys.get("recovery"):
            master = self._unwrap(keys["recovery"], _norm_recovery(secret))
        if master is None:
            self._fails.append(now)
            raise WrongPassphrase("That is not the passphrase.")
        self._fails = []
        self._key, self._until, self._notes = master, now + self._auto, {}
        self._load_all()

    def change_passphrase(self, old: str, new: str) -> None:
        if len(new) < 8:
            raise PrivateError("Use a passphrase of at least 8 characters.")
        keys = json.loads(self._keys_path.read_text(encoding="utf-8"))
        master = self._unwrap(keys["pass"], old.strip()) or (
            self._unwrap(keys["recovery"], _norm_recovery(old)) if keys.get("recovery") else None)
        if master is None:
            raise WrongPassphrase("That is not the passphrase.")
        keys["pass"] = self._wrap(master, new.strip())
        atomic_write_text(self._keys_path, json.dumps(keys), mode=0o600)

    # ---- notes ---------------------------------------------------------------------------------------
    def _path(self, ident: str) -> Path:
        if not re.fullmatch(r"p-[0-9a-f]{12}", ident):
            raise PrivateError("That is not a private note id.")
        return self.dir / f"{ident}.enc"

    def _seal(self, key: bytes, payload: dict) -> bytes:
        AESGCM, _s, _b = _crypto()
        nonce = os.urandom(12)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return MAGIC + nonce + AESGCM(key).encrypt(nonce, body, MAGIC + payload["id"].encode())

    def _open(self, key: bytes, ident: str, blob: bytes) -> dict:
        AESGCM, _s, InvalidTag = _crypto()
        if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
            raise PrivateError("That private note is damaged.")
        try:
            data = AESGCM(key).decrypt(blob[8:20], blob[20:], MAGIC + ident.encode())
        except InvalidTag as e:
            raise PrivateError("That private note is damaged or was changed.") from e
        return json.loads(data)

    def _write(self, payload: dict) -> None:
        path = self._path(payload["id"])
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self._seal(self._need(), payload))
        tmp.replace(path)

    def _load_all(self) -> None:
        for p in sorted(self.dir.glob("p-*.enc")):
            try:
                self._notes[p.stem] = self._open(self._key, p.stem, p.read_bytes())
            except (PrivateError, OSError, ValueError):
                continue  # a damaged file is skipped here and reported when it is opened

    def add(self, title: str, body: str, tags=()) -> str:
        self._need()
        title = " ".join(str(title).split())[:120]
        if not title:
            raise PrivateError("A private note needs a title.")
        now = self._clock()
        ident = "p-" + secrets.token_hex(6)
        payload = {"id": ident, "title": title, "body": str(body), "tags": [str(t) for t in tags][:8],
                   "created": now, "updated": now}
        self._write(payload)
        self._notes[ident] = payload
        return ident

    def get(self, ident: str) -> dict:
        key = self._need()
        path = self._path(ident)
        if not path.is_file():
            raise PrivateError("No such private note.")
        payload = self._open(key, ident, path.read_bytes())
        self._notes[ident] = payload
        return payload

    def update(self, ident: str, *, title: str | None = None, body: str | None = None, tags=None) -> dict:
        payload = dict(self.get(ident))
        if title is not None and " ".join(title.split()):
            payload["title"] = " ".join(title.split())[:120]
        if body is not None:
            payload["body"] = str(body)
        if tags is not None:
            payload["tags"] = [str(t) for t in tags][:8]
        payload["updated"] = self._clock()
        self._write(payload)
        self._notes[ident] = payload
        return payload

    def archive(self, ident: str) -> None:
        self._need()
        path = self._path(ident)
        if not path.is_file():
            raise PrivateError("No such private note.")
        path.rename(path.with_suffix(".enc.archived"))
        self._notes.pop(ident, None)

    def list(self) -> list[tuple[str, str, float]]:
        self._need()
        return [(n["id"], n["title"], n["updated"])
                for n in sorted(self._notes.values(), key=lambda n: -n["updated"])]

    def search(self, query: str, limit: int = 8) -> list[dict]:
        self._need()
        terms = {w for w in words(query)}
        scored = []
        for n in self._notes.values():
            title_w, body_w = set(words(n["title"])), set(words(n["body"] + " " + " ".join(n["tags"])))
            score = 3 * len(terms & title_w) + len(terms & body_w)
            if score:
                scored.append((score, n))
        scored.sort(key=lambda s: (-s[0], -s[1]["updated"]))
        return [{"id": n["id"], "title": n["title"], "score": s} for s, n in scored[:limit]]
