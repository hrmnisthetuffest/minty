"""Encrypted export and import of everything (615).

One file, AES-256-GCM under a key derived (scrypt) from a passphrase that is never stored. It holds your settings,
memory, history, the databases and the whole notes folder (with its git history). It never holds keys, the API token,
the panel password, standing approvals or the rebuildable search index. Importing writes into a new folder; it never
touches a live one. Large exports are split into parts that fit a Telegram file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from minty_core.backup import (DATABASES, INCLUDE_DIRS, INCLUDE_FILES, INCLUDE_RAW, _add_bytes, _crypto, _key,
                               _snapshot_db)

MAGIC = b"MINTYEX1"
PART_BYTES = 45 * 1024 * 1024
MAX_TOTAL = 800 * 1024 * 1024
MIN_PASSPHRASE = 8
EXTRA_FILES = ("contradictions.json", "memory.md", "instructions.md")
EXTRA_DATABASES = ("butler.db",)
NEVER = {"secrets.toml", "api_token", "panel_auth.toml", "standing.json", "backup.key"}


class ExportError(Exception):
    pass


@dataclass
class ExportInfo:
    path: Path
    size: int
    files: int
    sha256: str


@dataclass
class VerifyResult:
    ok: bool
    files: int = 0
    problems: list[str] = field(default_factory=list)


def _secret(passphrase: str) -> bytes:
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise ExportError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters.")
    return passphrase.strip().encode("utf-8")


def _collect(config_dir: Path, vault_root: Path | None) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    config_dir = Path(config_dir)
    names = tuple(dict.fromkeys((*INCLUDE_FILES, *EXTRA_FILES)))
    for name in names:
        if name not in NEVER and (config_dir / name).is_file():
            files[f"config/{name}"] = (config_dir / name).read_bytes()
    for folder in (*INCLUDE_DIRS, INCLUDE_RAW):
        base = config_dir / folder
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file():
                files[f"config/{p.relative_to(config_dir).as_posix()}"] = p.read_bytes()
    for name in (*DATABASES, *EXTRA_DATABASES):
        if (config_dir / name).is_file():
            try:
                files[f"config/{name}"] = _snapshot_db(config_dir / name)
            except sqlite3.Error as e:
                raise ExportError(f"could not read {name}: {type(e).__name__}") from e
    if vault_root is not None and Path(vault_root).is_dir():
        root = Path(vault_root)
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or rel.startswith("_minty/index.db") or rel == "_minty/changes.log":
                continue
            files[f"vault/{rel}"] = p.read_bytes()
    if sum(len(v) for v in files.values()) > MAX_TOTAL:
        raise ExportError("That is more than 800 MB; export the history separately.")
    return files


def export_everything(config_dir: Path, vault_root: Path | None, passphrase: str, out_path: Path) -> ExportInfo:
    secret = _secret(passphrase)
    AESGCM, _s, _b = _crypto()
    files = _collect(config_dir, vault_root)
    manifest = {"format": 1, "created": time.time(),
                "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        _add_bytes(tar, "manifest.json", json.dumps(manifest).encode())
        for name, data in files.items():
            _add_bytes(tar, name, data)
    salt, nonce = os.urandom(16), os.urandom(12)
    blob = MAGIC + salt + nonce + AESGCM(_key(secret, salt)).encrypt(nonce, raw.getvalue(), MAGIC)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    tmp.replace(out)
    return ExportInfo(out, len(blob), len(files), hashlib.sha256(blob).hexdigest())


def _open(path: Path, passphrase: str) -> tuple[dict, dict[str, bytes]]:
    secret = _secret(passphrase) if len(passphrase or "") >= MIN_PASSPHRASE else passphrase.encode()
    AESGCM, _s, InvalidTag = _crypto()
    blob = Path(path).read_bytes()
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise ExportError("This is not a Minty export.")
    salt, nonce, body = blob[8:24], blob[24:36], blob[36:]
    try:
        plain = AESGCM(_key(secret, salt)).decrypt(nonce, body, MAGIC)
    except InvalidTag as e:
        raise ExportError("Wrong passphrase, or the file was changed.") from e
    contents: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        for m in tar.getmembers():
            parts = Path(m.name).parts
            if m.isfile() and not m.name.startswith("/") and ".." not in parts:
                contents[m.name] = tar.extractfile(m).read()
    try:
        manifest = json.loads(contents.pop("manifest.json"))
    except (KeyError, ValueError) as e:
        raise ExportError("The export has no manifest.") from e
    return manifest, contents


def verify(path: Path, passphrase: str) -> VerifyResult:
    try:
        manifest, contents = _open(path, passphrase)
    except ExportError as e:
        return VerifyResult(False, 0, [str(e)])
    problems = []
    for name, digest in manifest.get("files", {}).items():
        if name not in contents:
            problems.append(f"{name} is missing")
        elif hashlib.sha256(contents[name]).hexdigest() != digest:
            problems.append(f"{name} fails its checksum")
    return VerifyResult(not problems, len(contents), problems)


def import_everything(path: Path, passphrase: str, target: Path) -> int:
    """Decrypt, check, and extract into `target/config` and `target/vault`. `target` must be new or empty."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise ExportError(f"{target} is not empty; import into a new folder.")
    manifest, contents = _open(path, passphrase)
    for name, digest in manifest.get("files", {}).items():
        if name in contents and hashlib.sha256(contents[name]).hexdigest() != digest:
            raise ExportError(f"{name} fails its checksum; the export is damaged.")
    n = 0
    for name, data in contents.items():
        if not name.startswith(("config/", "vault/")):
            continue  # nothing else is ever written
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        n += 1
    return n


# ---- parts that fit a Telegram file ----------------------------------------------------------------------
def split_file(path: Path, max_bytes: int = PART_BYTES) -> list[Path]:
    path = Path(path)
    parts: list[Path] = []
    with path.open("rb") as f:
        i = 1
        while True:
            chunk = f.read(max_bytes)
            if not chunk:
                break
            part = path.with_name(f"{path.name}.part{i:03d}")
            part.write_bytes(chunk)
            os.chmod(part, 0o600)
            parts.append(part)
            i += 1
    return parts


def join_parts(first: Path, out: Path) -> Path:
    first = Path(first)
    m = re.fullmatch(r"(.+)\.part(\d{3})", first.name)
    if not m or m.group(2) != "001":
        raise ExportError("Give the first part (the one ending in .part001).")
    parts, i = [], 1
    while (first.with_name(f"{m.group(1)}.part{i:03d}")).is_file():
        parts.append(first.with_name(f"{m.group(1)}.part{i:03d}"))
        i += 1
    out = Path(out)
    with out.open("wb") as f:
        for p in parts:
            f.write(p.read_bytes())
    os.chmod(out, 0o600)
    return out


# ---- the terminal command: minty transfer export|verify|import ---------------------------------------
def cli(argv: list[str], config_dir: Path, vault_root: Path | None, workspace: Path, ask_secret=None) -> int:
    import getpass
    from datetime import datetime

    ask = ask_secret or getpass.getpass
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "export":
            out, split = None, False
            i = 0
            while i < len(rest):
                if rest[i] == "--out" and i + 1 < len(rest):
                    out, i = Path(rest[i + 1]), i + 2
                elif rest[i] == "--split":
                    split, i = True, i + 1
                else:
                    raise ExportError(f"I don't know {rest[i]!r}.")
            first = ask("Passphrase for the export (8+ characters; it is not stored): ")
            if ask("Again: ") != first:
                print("They did not match; nothing was made.")
                return 1
            out = out or Path(workspace) / "exports" / f"minty-export-{datetime.now():%Y%m%d-%H%M}.hdx"
            info = export_everything(config_dir, vault_root, first, out)
            print(f"Wrote {info.path} ({info.size // 1024} KB, {info.files} files). Your keys and the API token are "
                  "not included; nothing here can be read without the passphrase.")
            if split:
                parts = split_file(info.path, PART_BYTES)
                print(f"Split into {len(parts)} part{'s' * (len(parts) != 1)} that each fit a Telegram file: "
                      f"{parts[0].name} ... {parts[-1].name}. Join them again with: minty transfer join "
                      f"{parts[0].name} <output>")
            return 0
        if cmd == "verify" and rest:
            res = verify(Path(rest[0]), ask("Passphrase: "))
            print(f"The export looks good ({res.files} files)." if res.ok else "Problem: " + "; ".join(res.problems))
            return 0 if res.ok else 1
        if cmd == "import" and len(rest) == 2:
            n = import_everything(Path(rest[0]), ask("Passphrase: "), Path(rest[1]))
            print(f"Imported {n} files into {rest[1]} (config/ and vault/). Nothing live was touched; look it over, "
                  "then copy what you want where you want it.")
            return 0
        if cmd == "join" and len(rest) == 2:
            print(f"Joined into {join_parts(Path(rest[0]), Path(rest[1]))}")
            return 0
    except ExportError as e:
        print(e)
        return 1
    except FileNotFoundError as e:
        print(f"I can't find {e.filename or 'that file'}. Make the export first (and check the name).")
        return 1
    print("usage: minty transfer export [--out FILE] [--split] | verify FILE | import FILE NEWFOLDER | "
          "join FIRSTPART OUT")
    return 1
