"""Configuration helpers: snapshots, a readable diff, and validation errors in plain words."""
from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import tomli_w
from pydantic import ValidationError


@dataclass(frozen=True)
class Change:
    path: str
    old: object
    new: object

    def words(self) -> str:
        if self.old is None:
            return f"{self.path} was added: {_show(self.new)}"
        if self.new is None:
            return f"{self.path} was removed (it was {_show(self.old)})"
        return f"{self.path}: {_show(self.old)} -> {_show(self.new)}"


def _show(value) -> str:
    text = repr(value) if not isinstance(value, bool) else ("on" if value else "off")
    return text if len(text) <= 60 else text[:57] + "..."


def diff_dicts(old: dict, new: dict, prefix: str = "") -> list[Change]:
    out: list[Change] = []
    for key in sorted(set(old) | set(new)):
        path = f"{prefix}{key}"
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            out += diff_dicts(a, b, path + ".")
        elif a != b:
            out.append(Change(path, a, b))
    return out


class ConfigSnapshots:
    """A copy of the settings after every change (the newest `keep` are kept), to see what changed and to go back."""

    def __init__(self, directory: Path, keep: int = 20, clock: Callable[[], float] = time.time):
        self.directory, self._keep, self._clock = Path(directory), keep, clock

    def list(self) -> list[Path]:
        return sorted(self.directory.glob("*.toml")) if self.directory.exists() else []

    def _load(self, path: Path) -> dict:
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def take(self, config: dict) -> Path | None:
        """Save `config` unless it equals the newest snapshot. Returns the file, or None when nothing changed."""
        snaps = self.list()
        if snaps and self._load(snaps[-1]) == config:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        n = 0
        path = self.directory / f"{stamp}-{n:02d}.toml"  # the counter keeps same-second snapshots in order
        while path.exists():
            n += 1
            path = self.directory / f"{stamp}-{n:02d}.toml"
        path.write_text(tomli_w.dumps(config), encoding="utf-8")
        for old in self.list()[:-self._keep]:
            old.unlink(missing_ok=True)
        return path

    def last_change(self) -> list[Change]:
        """What the newest snapshot changed compared with the one before it."""
        snaps = self.list()
        if len(snaps) < 2:
            return []
        return diff_dicts(self._load(snaps[-2]), self._load(snaps[-1]))

    def since(self, index_from_end: int = 1) -> dict:
        snaps = self.list()
        return self._load(snaps[-1 - index_from_end]) if len(snaps) > index_from_end else {}


def explain_validation(error: ValidationError) -> list[str]:
    """One plain line per problem: where, what is wrong, what is allowed."""
    lines = []
    for e in error.errors():
        where = ".".join(str(p) for p in e.get("loc", ())) or "settings"
        kind, ctx, got = e.get("type", ""), e.get("ctx") or {}, e.get("input")
        shown = f" (you have {got!r})" if got is not None and not isinstance(got, (dict, list)) else ""
        if kind == "literal_error":
            lines.append(f"{where}: must be one of {ctx.get('expected', '?')}{shown}")
        elif kind in ("int_parsing", "int_from_float", "int_type"):
            lines.append(f"{where}: must be a whole number{shown}")
        elif kind in ("float_parsing", "float_type"):
            lines.append(f"{where}: must be a number{shown}")
        elif kind in ("bool_parsing", "bool_type"):
            lines.append(f"{where}: must be true or false{shown}")
        elif kind == "greater_than_equal":
            lines.append(f"{where}: must be at least {ctx.get('ge')}{shown}")
        elif kind == "less_than_equal":
            lines.append(f"{where}: must be at most {ctx.get('le')}{shown}")
        elif kind == "greater_than":
            lines.append(f"{where}: must be more than {ctx.get('gt')}{shown}")
        elif kind == "string_type":
            lines.append(f"{where}: must be text{shown}")
        elif kind == "extra_forbidden":
            lines.append(f"{where}: is not a setting I know")
        elif kind == "missing":
            lines.append(f"{where}: is required")
        else:
            lines.append(f"{where}: {e.get('msg', 'is not valid')}{shown}")
    return lines
