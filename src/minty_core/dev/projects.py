"""Which folders count as your projects: git folders directly under `dev.root`, plus any you list by name. A name that does
not resolve to one of them (a path, `..`, a link pointing elsewhere) is refused."""
from __future__ import annotations

from pathlib import Path


class Projects:
    def __init__(self, root, extra: dict[str, str] | None = None):
        self.root = Path(str(root)).expanduser()
        self.extra = {k.lower(): Path(str(v)).expanduser() for k, v in (extra or {}).items()}

    def _under_root(self) -> dict[str, Path]:
        found = {}
        try:
            real_root = self.root.resolve()
            for d in sorted(self.root.iterdir()):
                real = d.resolve()
                if d.is_dir() and (real / ".git").exists() and real.parent == real_root:
                    found[d.name.lower()] = real
        except OSError:
            pass
        return found

    def names(self) -> list[str]:
        out = {**self._under_root(), **{k: v.resolve() for k, v in self.extra.items() if (v / ".git").exists()}}
        return sorted(out)

    def resolve(self, name: str) -> Path:
        key = str(name or "").strip().lower()
        if not key or "/" in key or key.startswith("."):
            raise ValueError("Name one of your projects: " + (", ".join(self.names()) or "none are set up yet") + ".")
        known = {**self._under_root(), **{k: v.resolve() for k, v in self.extra.items() if v.is_dir()}}
        if key not in known:
            raise ValueError(f"{name!r} is not one of your projects: " + (", ".join(sorted(known)) or "none") + ".")
        return known[key]
