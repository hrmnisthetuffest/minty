"""What is this file? (767) Its type from the first bytes, what it is for from where it lives, and whether it is safe to
delete. Advice only: nothing is deleted or moved. Files that look like secrets are named but never read."""
from __future__ import annotations

import os
import re
from pathlib import Path

SECRET_NAME = re.compile(r"(^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx|kdbx|gpg)$|^\.env(\.|$)|^credentials|^\.netrc$|"
                         r"^secrets?\.|\.token$)", re.I)
MAGIC = [(b"\x89PNG\r\n\x1a\n", "a PNG image"), (b"\xff\xd8\xff", "a JPEG image"), (b"GIF8", "a GIF image"),
         (b"%PDF", "a PDF document"), (b"\x7fELF", "a program (compiled)"), (b"SQLite format 3\0", "a SQLite database"),
         (b"\x1f\x8b", "a gzip-compressed file"), (b"BZh", "a bzip2-compressed file"), (b"\xfd7zXZ", "an xz-compressed file"),
         (b"7z\xbc\xaf", "a 7-zip archive"), (b"ID3", "an MP3 audio file"), (b"OggS", "an Ogg audio or video file"),
         (b"fLaC", "a FLAC audio file"), (b"#!", "a script")]
ZIP_KINDS = {".docx": "a Word document", ".xlsx": "an Excel spreadsheet", ".pptx": "a PowerPoint presentation",
             ".odt": "an OpenDocument text", ".jar": "a Java archive", ".apk": "an Android package", ".epub": "an e-book"}
SYSTEM_ROOTS = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/var/lib", "/opt", "/sys", "/proc", "/dev")
OWN_FOLDERS = ("Documents", "Pictures", "Desktop", "Videos", "Music", "Downloads")


def _size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB" if n >= 1024 else f"{n} bytes"


def _kind(path: Path, head: bytes, size: int) -> str:
    if head.startswith(b"PK\x03\x04"):
        return ZIP_KINDS.get(path.suffix.lower(), "a zip archive")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "a WebP image"
    if head[4:8] == b"ftyp":
        return "an MP4 video or audio file"
    for magic, name in MAGIC:
        if head.startswith(magic):
            return name
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary data I do not recognise"
    if not head:
        return "an empty file"
    if size <= 1048576:
        with open(path, "rb") as f:
            lines = f.read().count(b"\n")
        return f"plain text, {lines} line{'s' * (lines != 1)}"
    return "plain text (large)"


def _advice(path: Path, home: Path, tmp_dirs) -> str:
    s = str(path)
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        if any(s == t or s.startswith(t.rstrip("/") + "/") for t in tmp_dirs):
            return "A temporary file; usually safe to delete."
        if s.startswith(SYSTEM_ROOTS):
            return "It is a system file. Do not delete it; it belongs to the system or an installed program."
        return "I cannot tell if you still need it, and I will not delete anything."
    if ".git" in parts:
        return "It is part of a Git repository's history. Do not delete it."
    if ".ssh" in parts or ".gnupg" in parts:
        return "It belongs to your keys. Do not delete it."
    if ".cache" in parts:
        return "Cached data that programs rebuild; usually safe to delete."
    if "__pycache__" in parts or path.suffix == ".pyc":
        return "Compiled Python cache; usually safe to delete."
    if "node_modules" in parts:
        return "Installed JavaScript packages; safe to delete, 'npm install' brings them back."
    if "Trash" in parts:
        return "It is in the Trash; safe to delete."
    if ".config" in parts or parts[:2] == (".local", "share"):
        return "An app's settings or data. Do not delete it unless you want that app reset."
    if path.suffix == ".log":
        return "A log file; usually safe to delete when old."
    if parts and parts[0] in OWN_FOLDERS:
        return "It is your own file; ask yourself whether you still need it. I would not delete it for you."
    return "I cannot tell if you still need it, and I will not delete anything."


def describe_path(path, home, tmp_dirs=("/tmp", "/var/tmp")) -> str:
    p, home = Path(str(path)).expanduser(), Path(home)
    if not p.is_symlink() and not p.exists():
        return f"{p} does not exist."
    prefix = ""
    if p.is_symlink():
        target = Path(os.path.realpath(p))
        prefix = f"{p.name} is a link to {target}. "
        if not target.exists():
            return prefix + "The target is gone, so the link is broken and safe to delete."
        p = target
    if p.is_dir():
        try:
            count = sum(1 for _ in zip(range(5000), p.iterdir()))
        except OSError:
            return prefix + f"{p} is a folder I cannot open."
        return prefix + f"{p.name or p} is a folder with {count}{'+' if count >= 5000 else ''} item" \
            f"{'s' * (count != 1)}. {_advice(p, home, tmp_dirs)}"
    if SECRET_NAME.search(p.name) or ".ssh" in p.parts or ".gnupg" in p.parts:
        what = "a private key" if re.match(r"^id_|\.(pem|key)$", p.name) else "a file that likely holds secrets"
        return prefix + f"{p.name} looks like {what}. I did not read it. {_advice(p, home, tmp_dirs)}"
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            head = f.read(4096)
    except OSError:
        return prefix + f"{p.name} exists but I cannot read it."
    return prefix + f"{p.name} is {_kind(p, head, size)}, {_size(size)}. {_advice(p, home, tmp_dirs)}"
