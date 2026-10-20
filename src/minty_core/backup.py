"""Encrypted backups of what is yours (memory, notes, settings, history summaries), and a restore test.

Format: b"MINTYBK1" + 16-byte salt + 12-byte nonce + AES-256-GCM(tar.gz). The key comes from scrypt over a passphrase
or over the contents of `backup.key` (made once, mode 0600; keep a copy somewhere else, without it a backup cannot
be opened). Never included: keys, panel password, API token, browser profile, traces, audit logs. Restoring never
overwrites a live folder; it extracts to a new one. A backup is only trusted after `verify` has decrypted it,
checked every file's checksum and opened the settings and the databases.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"MINTYBK1"
SCRYPT = dict(n=2**14, r=8, p=1)
INCLUDE_FILES = ("config.toml", "memory.md", "instructions.md", "learned.json", "commands.json", "proposals.json")
INCLUDE_DIRS = ("personalities", "history/days", "history/months", "config-snapshots")
INCLUDE_RAW = "history/raw"
DATABASES = ("memory.db", "schedule.db", "inbox.db", "library.db", "health.db", "finance.db")
KEEP = 7


class BackupError(Exception):
    pass


def _crypto():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as e:
        raise BackupError("the 'cryptography' package is not installed (pip install -e '.[backup]')") from e
    return AESGCM, Scrypt, InvalidTag


def _key(secret: bytes, salt: bytes) -> bytes:
    _aes, scrypt, _bad = _crypto()
    return scrypt(salt=salt, length=32, **SCRYPT).derive(secret)


def load_or_make_keyfile(path: Path) -> bytes:
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = os.urandom(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return data


@dataclass
class BackupInfo:
    path: Path
    size: int
    files: int
    sha256: str  # of the encrypted file


@dataclass
class VerifyResult:
    ok: bool
    files: int = 0
    problems: list[str] = field(default_factory=list)


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
    tar.addfile(info, io.BytesIO(data))


def _snapshot_db(path: Path) -> bytes:
    """A consistent copy of a database that may be in use (the WAL is applied)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / path.name
        src, dst = sqlite3.connect(path, timeout=10), sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return target.read_bytes()


def create(directory: Path, secret: bytes, out_path: Path, *, include_raw: bool = False) -> BackupInfo:
    AESGCM, _s, _b = _crypto()
    directory, files = Path(directory), {}
    for name in INCLUDE_FILES:
        if (directory / name).is_file():
            files[name] = (directory / name).read_bytes()
    for folder in INCLUDE_DIRS + ((INCLUDE_RAW,) if include_raw else ()):
        for p in sorted((directory / folder).rglob("*")) if (directory / folder).is_dir() else []:
            if p.is_file():
                files[str(p.relative_to(directory))] = p.read_bytes()
    for name in DATABASES:
        if (directory / name).is_file():
            try:
                files[name] = _snapshot_db(directory / name)
            except sqlite3.Error as e:
                raise BackupError(f"could not read {name}: {type(e).__name__}") from e
    manifest = {"created": time.time(), "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        _add_bytes(tar, "manifest.json", json.dumps(manifest).encode())
        for name, data in files.items():
            _add_bytes(tar, name, data)
    salt, nonce = os.urandom(16), os.urandom(12)
    blob = MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, raw.getvalue(), MAGIC)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    tmp.replace(out_path)
    return BackupInfo(out_path, len(blob), len(files), hashlib.sha256(blob).hexdigest())


def _open(path: Path, secret: bytes) -> tuple[dict, dict[str, bytes]]:
    AESGCM, _s, InvalidTag = _crypto()
    blob = Path(path).read_bytes()
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise BackupError("this is not a Minty backup")
    salt, nonce, body = blob[8:24], blob[24:36], blob[36:]
    try:
        plain = AESGCM(_key(secret, salt)).decrypt(nonce, body, MAGIC)
    except InvalidTag as e:
        raise BackupError("wrong passphrase or key file, or the file was changed") from e
    contents: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and not member.name.startswith(("/", "..")) and ".." not in Path(member.name).parts:
                contents[member.name] = tar.extractfile(member).read()
    try:
        manifest = json.loads(contents.pop("manifest.json"))
    except (KeyError, ValueError) as e:
        raise BackupError("the backup has no manifest") from e
    return manifest, contents


def verify(path: Path, secret: bytes) -> VerifyResult:
    """The restore test: decrypt, check each file's checksum, open the settings and every database."""
    try:
        manifest, contents = _open(path, secret)
    except BackupError as e:
        return VerifyResult(False, 0, [str(e)])
    problems: list[str] = []
    for name, digest in manifest.get("files", {}).items():
        if name not in contents:
            problems.append(f"{name} is missing")
        elif hashlib.sha256(contents[name]).hexdigest() != digest:
            problems.append(f"{name} fails its checksum")
    if "config.toml" in contents:
        try:
            tomllib.loads(contents["config.toml"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            problems.append("config.toml does not parse")
    with tempfile.TemporaryDirectory() as tmp:
        for name in DATABASES:
            if name in contents:
                target = Path(tmp) / name
                target.write_bytes(contents[name])
                try:
                    db = sqlite3.connect(target)
                    state = db.execute("PRAGMA integrity_check").fetchone()[0]
                    db.close()
                    if state != "ok":
                        problems.append(f"{name} is damaged ({state})")
                except sqlite3.Error as e:
                    problems.append(f"{name} cannot be opened ({type(e).__name__})")
    return VerifyResult(not problems, len(contents), problems)


def restore(path: Path, secret: bytes, target: Path) -> int:
    """Extract into `target` (which must not exist or be empty). Never into a live folder by accident."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise BackupError(f"{target} is not empty; restore into a new folder")
    _manifest, contents = _open(path, secret)
    for name, data in contents.items():
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return len(contents)


def prune(folder: Path, keep: int = KEEP) -> list[str]:
    old = sorted(Path(folder).glob("minty-backup-*.bak"))[:-keep]
    for p in old:
        p.unlink(missing_ok=True)
    return [p.name for p in old]
