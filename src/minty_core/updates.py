"""Update policy: nothing updates itself. A staged update is prepare -> (you pull) -> check, and a failed check
rolls the checkout back to the commit that was good.

`prepare` records the current commit and a copy of the settings and freezes the installed package versions.
`check` runs the full tests and the doctor and compares the installed packages with the freeze. Everything that
touches the system goes through `run`, so the logic is testable without a repository.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

KEY_MAX_AGE_DAYS = 90


def run(argv: list[str], cwd: Path) -> tuple[int, str]:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout + done.stderr)[-4000:]


@dataclass
class CheckResult:
    ok: bool
    steps: list[str] = field(default_factory=list)
    rollback: str = ""


def old_keys(ages: dict[str, int], max_days: int = KEY_MAX_AGE_DAYS) -> list[str]:
    return sorted(n for n, d in ages.items() if d >= max_days)


def key_reminder(ages: dict[str, int], max_days: int = KEY_MAX_AGE_DAYS) -> str:
    old = old_keys(ages, max_days)
    if not old:
        return ""
    names = ", ".join(f"{n} ({ages[n]} days)" for n in old[:5])
    return (f"These keys have not been changed for {max_days} days or more: {names}. "
            "Rotating them now and then limits the damage if one ever leaks.")


def prepare(root: Path, config_dir: Path, runner: Callable = run, now: Callable[[], float] = time.time) -> dict:
    """Record what to go back to. Does not change anything else."""
    code, head = runner(["git", "rev-parse", "HEAD"], root)
    if code != 0:
        raise RuntimeError("this is not a git checkout")
    code, status = runner(["git", "status", "--porcelain"], root)
    _c, frozen = runner(["pip", "freeze"], root)
    state = {"commit": head.strip(), "clean": not status.strip(), "at": now(), "freeze": frozen.strip().splitlines()}
    folder = Path(config_dir) / "update-state"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "last-good.json").write_text(json.dumps(state), encoding="utf-8")
    if (Path(config_dir) / "config.toml").exists():
        shutil.copy2(Path(config_dir) / "config.toml", folder / "config.toml.before-update")
    return state


def drift(state: dict, runner: Callable = run, root: Path | None = None) -> list[str]:
    """Packages whose installed version differs from the freeze taken at prepare time."""
    _c, now = runner(["pip", "freeze"], root or Path("."))
    before = {line.split("==")[0].lower(): line for line in state.get("freeze", []) if "==" in line}
    after = {line.split("==")[0].lower(): line for line in now.strip().splitlines() if "==" in line}
    changed = [f"{before[n]} -> {after[n]}" for n in sorted(before) if n in after and before[n] != after[n]]
    changed += [f"{n} removed" for n in sorted(before) if n not in after]
    return changed


def check(root: Path, config_dir: Path, runner: Callable = run) -> CheckResult:
    """Tests and doctor; on failure, the exact command that goes back."""
    path = Path(config_dir) / "update-state" / "last-good.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckResult(False, ["no prepared state: run `staged_update.py prepare` before pulling"])
    rollback = f"git checkout {state['commit']}"
    steps: list[str] = []
    code, out = runner(["python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], root)
    steps.append(f"tests: {'passed' if code == 0 else 'FAILED'} — {out.strip().splitlines()[-1] if out.strip() else ''}")
    if code != 0:
        return CheckResult(False, steps, rollback)
    code, out = runner(["python", "scripts/doctor.py"], root)
    steps.append(f"doctor: {'ok' if code == 0 else 'reports problems'}")
    if code != 0:
        return CheckResult(False, steps, rollback)
    changed = drift(state, runner, root)
    steps.append("packages: " + (", ".join(changed) if changed else "unchanged"))
    return CheckResult(True, steps, rollback)
