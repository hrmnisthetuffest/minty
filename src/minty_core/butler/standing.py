"""Standing approvals (1065): a limited, expiring yes you give once for one small action.

Strictly narrower than a `safety.rules` allow: an exact tool name, exact argument values, a per-day cap and an
expiry. It never covers class 3, a call that carries untrusted data or a new destination, or anything during a
lockdown (the gate checks those before it asks this). Only you can make one: a terminal command. There is no tool
a model can call to create or widen one, and Telegram can only list and revoke.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text

DAY = 86400
MAX_DAYS = 30
MAX_PER_DAY = 50
_TOOL = re.compile(r"^[a-z0-9_]+$")


class StandingError(ValueError):
    pass


@dataclass
class Grant:
    id: int
    tool: str
    args: dict = field(default_factory=dict)
    max_per_day: int = 5
    expires: float = 0.0
    created: float = 0.0
    note: str = ""
    uses: list = field(default_factory=list)
    revoked: bool = False

    def describe(self) -> str:
        shown = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"#{self.id} {self.note or self.tool} ({self.tool}{' ' + shown if shown else ''})"


class StandingApprovals:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"next": 1, "grants": []}
        except (OSError, ValueError):
            return {"next": 1, "grants": []}

    def _save(self, data: dict) -> None:
        atomic_write_text(self.path, json.dumps(data, indent=1), mode=0o600)

    def _grants(self, data: dict) -> list[Grant]:
        out = []
        for g in data.get("grants", []):
            try:
                out.append(Grant(**g))
            except TypeError:
                continue
        return out

    # ---- making and ending ---------------------------------------------------------------------------
    def grant(self, tool: str, *, args: dict | None = None, max_per_day: int = 5, days: int = 7,
              note: str = "") -> Grant:
        if not _TOOL.match(str(tool or "")):
            raise StandingError("A standing approval needs one exact tool name (no wildcards or spaces).")
        if not 1 <= int(max_per_day) <= MAX_PER_DAY:
            raise StandingError(f"The daily cap must be between 1 and {MAX_PER_DAY}.")
        if not 1 <= int(days) <= MAX_DAYS:
            raise StandingError(f"A standing approval lasts between 1 and {MAX_DAYS} days.")
        data = self._load()
        now = self._clock()
        g = Grant(int(data.get("next", 1)), tool, {str(k): str(v) for k, v in (args or {}).items()},
                  int(max_per_day), now + int(days) * DAY, now, " ".join(str(note).split())[:60])
        data["next"] = g.id + 1
        data.setdefault("grants", []).append(asdict(g))
        self._save(data)
        return g

    def revoke(self, ident: int) -> bool:
        data = self._load()
        for g in data.get("grants", []):
            if g.get("id") == ident and not g.get("revoked"):
                g["revoked"] = True
                self._save(data)
                return True
        return False

    # ---- using ---------------------------------------------------------------------------------------
    def active(self) -> list[Grant]:
        now = self._clock()
        return [g for g in self._grants(self._load()) if not g.revoked and g.expires > now]

    def match(self, name: str, args: dict, risk: int) -> Grant | None:
        """The grant that covers exactly this call, or None. Class 3 is never covered."""
        if risk >= 3:
            return None
        now = self._clock()
        args = args if isinstance(args, dict) else {}
        for g in self.active():
            if g.tool != name:
                continue
            if any(str(args.get(k, "")).strip().lower() != v.strip().lower() for k, v in g.args.items()):
                continue
            if len([t for t in g.uses if t > now - DAY]) >= g.max_per_day:
                continue
            return g
        return None

    def use(self, grant: Grant) -> None:
        data = self._load()
        now = self._clock()
        for g in data.get("grants", []):
            if g.get("id") == grant.id:
                g["uses"] = [t for t in g.get("uses", []) if t > now - 8 * DAY] + [now]
        self._save(data)

    # ---- review --------------------------------------------------------------------------------------
    def review(self) -> str:
        now = self._clock()
        lines = []
        for g in self.active():
            n = len([t for t in g.uses if t > now - 7 * DAY])
            used = f"used {n} time{'s' * (n != 1)} this week" if n else "not used this week"
            until = datetime.fromtimestamp(g.expires).strftime("%d %b")
            lines.append(f"{g.describe()}: {used}, cap {g.max_per_day} a day, until {until}"
                         + ("; revoke it if you do not need it" if not n else ""))
        return "Standing approvals:\n" + "\n".join(lines) if lines else "You have no standing approvals."


def run_cli(argv: list[str], path: Path, clock: Callable[[], float] = time.time) -> int:
    """`minty butler standing ...`: the only place a grant can be made."""
    sa = StandingApprovals(path, clock)
    cmd, rest = (argv[0], argv[1:]) if argv else ("list", [])
    try:
        if cmd == "grant" and rest:
            tool, opts = rest[0], rest[1:]
            args, per_day, days, note = {}, 5, 7, ""
            i = 0
            while i < len(opts):
                flag, val = opts[i], opts[i + 1] if i + 1 < len(opts) else ""
                if flag == "--arg" and "=" in val:
                    k, _, v = val.partition("=")
                    args[k] = v
                elif flag == "--per-day":
                    per_day = int(val)
                elif flag == "--days":
                    days = int(val)
                elif flag == "--note":
                    note = val
                else:
                    raise StandingError(f"I don't know the option {flag!r}.")
                i += 2
            g = sa.grant(tool, args=args, max_per_day=per_day, days=days, note=note)
            print(f"Granted {g.describe()}, {g.max_per_day} a day, until "
                  f"{datetime.fromtimestamp(g.expires):%d %b %Y}. Class 3 actions are never covered.")
            return 0
        if cmd in ("list", "review"):
            print(sa.review())
            return 0
        if cmd == "revoke" and rest:
            print("Revoked." if sa.revoke(int(rest[0])) else "There is no active standing approval with that number.")
            return 0
    except (StandingError, ValueError) as e:
        print(e)
        return 1
    print("usage: minty butler standing grant <tool> [--arg k=v] [--per-day N] [--days D] [--note text] | "
          "list | revoke <n>")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_cli(sys.argv[1:], Path("standing.json")))
