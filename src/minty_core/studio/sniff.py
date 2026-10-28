"""What a file really is, from its first bytes. The studio never goes by a file's name or by what a server called it: a playlist or a
concat list named `clip.mp4` would reach ffmpeg's playlist readers (which can name other files and addresses), and a document named
`clip.mp4` could end up posted. Only these binary media formats are accepted."""
from __future__ import annotations

from pathlib import Path

VIDEO_TYPES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".ts"})
AUDIO_TYPES = frozenset({".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"})
IMAGE_TYPES = frozenset({".jpg", ".png", ".webp", ".gif"})
_PNG = b"\x89PNG\r\n\x1a\n"


def sniff_bytes(b: bytes) -> str | None:
    """The extension that belongs to these leading bytes, or None when they are not one of the accepted media formats."""
    if b[4:8] == b"ftyp":
        brand = b[8:12]
        return ".mov" if brand == b"qt  " else ".m4a" if brand in (b"M4A ", b"M4B ") else ".mp4"
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm" if b"webm" in b[:64] else ".mkv"
    if b[:4] == b"RIFF":
        return {b"AVI ": ".avi", b"WAVE": ".wav", b"WEBP": ".webp"}.get(b[8:12])
    if b[:4] == b"OggS":
        return ".ogg"
    if b[:4] == b"fLaC":
        return ".flac"
    if b[:3] == b"ID3":
        return ".mp3"
    if b[:8] == _PNG:
        return ".png"
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(b) >= 2 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0 and (b[1] >> 3) & 3 != 1:
        return ".aac" if (b[1] >> 1) & 3 == 0 else ".mp3"  # layer bits 00: AAC in ADTS frames; otherwise an MPEG audio frame
    if len(b) > 376 and b[0] == b[188] == b[376] == 0x47:
        return ".ts"
    return None


def sniff(path) -> str | None:
    try:
        with open(path, "rb") as f:
            return sniff_bytes(f.read(4096))
    except OSError:
        return None
