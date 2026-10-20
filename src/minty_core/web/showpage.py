"""A plain local page for "show me" (572): results laid out for reading, with every piece of text escaped, no scripts, no
remote resources (a Content-Security-Policy says so too) and only http(s) links."""
from __future__ import annotations

import html
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
STYLE = ("body{font:17px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1b1b1b;"
         "background:#fafafa}h1{font-size:1.5rem}article{border-top:1px solid #ccc;padding:.8rem 0}"
         "h2{font-size:1.1rem;margin:0}a{color:#0b57d0}small{color:#555}")


def _link(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    return str(url).strip() if parts.scheme in ("http", "https") and parts.hostname else ""


def render_page(title: str, items: list[dict], note: str = "") -> str:
    esc = lambda s: html.escape(str(s or ""), quote=True)  # noqa: E731
    rows = []
    for it in items:
        link = _link(it.get("url", ""))
        head = esc(it.get("title", ""))
        if link:
            head = f'<a href="{esc(link)}" rel="noopener noreferrer">{head}</a>'
        small = f"<small>{esc(urlsplit(link).hostname)}</small>" if link else ""
        rows.append(f"<article><h2>{head}</h2><p>{esc(it.get('text', ''))}</p>{small}</article>")
    note_html = f"<p><small>{esc(note)}</small></p>" if note else ""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)}</title><style>{STYLE}</style></head><body><h1>{esc(title)}</h1>{note_html}"
            + "".join(rows) + "</body></html>")


def write_page(folder: Path, title: str, items: list[dict], now: datetime | None = None, note: str = "") -> Path:
    now = now or datetime.now()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"show-{now:%Y%m%d-%H%M%S}.html"
    path.write_text(render_page(title, items, note), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def open_page(path: Path, run: Callable[[list[str]], int] | None = None) -> bool:
    cmd = ["xdg-open", str(path)]
    try:
        return (run or (lambda c: subprocess.run(c, check=False, timeout=10, capture_output=True).returncode))(cmd) == 0
    except (OSError, subprocess.SubprocessError):
        return False
