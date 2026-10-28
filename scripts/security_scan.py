#!/usr/bin/env python3
"""Repeatable security scan: static analysis (bandit) against an accepted baseline, and known vulnerabilities in the installed packages
(pip-audit, which asks OSV over the network).

  python scripts/security_scan.py                 # both
  python scripts/security_scan.py --no-audit      # static analysis only (works offline)

Needs `bandit` and `pip-audit` on PATH, or `uv` (it then runs them with `uvx`, outside your environments). Exit status 1 when bandit
finds more of a rule than scripts/security_baseline.json accepts, or pip-audit reports an advisory. A tool that cannot run is said
so: that is not a pass.

What the baseline holds, and why it is not noise: B608 are SQL strings built from constant column lists or from allowlisted names
(never from a request: each site was read); B324 is SHA-1 used for ids and de-duplication, not for security; B108 is /tmp inside the
sandbox or text; B314 is ElementTree on RSS/arXiv answers already capped at 1 MB, where the bundled expat refuses entity bombs and
ElementTree never fetches an entity. Any NEW finding, or more of one rule than listed, fails the scan until it is read and either
fixed or added here with a reason.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "security_baseline.json"


def over_baseline(results: list[dict], baseline: dict[str, int]) -> list[str]:
    """Rules found more often than the baseline allows (a rule not in the baseline allows none)."""
    found = Counter(r["test_id"] for r in results)
    return [f"{rule}: {n} found, {baseline.get(rule, 0)} accepted" for rule, n in sorted(found.items()) if n > baseline.get(rule, 0)]


def _tool(name: str) -> list[str] | None:
    if shutil.which(name):
        return [name]
    return ["uvx", name] if shutil.which("uvx") else None


def run_bandit() -> tuple[list[dict] | None, str]:
    cmd = _tool("bandit")
    if cmd is None:
        return None, "bandit is not installed (pip install bandit, or install uv)"
    done = subprocess.run([*cmd, "-r", "src", "-q", "-f", "json", "--severity-level", "medium"], cwd=ROOT, capture_output=True, text=True)
    try:
        return json.loads(done.stdout)["results"], ""
    except (ValueError, KeyError):
        return None, f"bandit gave no result: {done.stderr.strip()[-200:]}"


def run_pip_audit() -> tuple[list[str] | None, str]:
    cmd = _tool("pip-audit") if shutil.which("pip-audit") else (["uvx", "pip-audit"] if shutil.which("uvx") else None)
    if cmd is None:
        return None, "pip-audit is not installed (pip install pip-audit, or install uv)"
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True, text=True).stdout
    done = subprocess.run([*cmd, "--no-deps", "--disable-pip", "-f", "json", "-r", "/dev/stdin"], input=freeze, capture_output=True, text=True)
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return None, f"pip-audit gave no result (offline?): {done.stderr.strip()[-200:]}"
    return [f"{d['name']} {d['version']}: {', '.join(v['id'] for v in d['vulns'])}" for d in data["dependencies"] if d.get("vulns")], ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-audit", action="store_true", help="skip pip-audit (it needs the network)")
    args = ap.parse_args(argv)
    bad = 0
    results, why = run_bandit()
    if results is None:
        print(f"static analysis: NOT RUN ({why})")
        bad = 1
    else:
        extra = over_baseline(results, json.loads(BASELINE.read_text()))
        print(f"static analysis: {len(results)} medium+ findings, {len(extra)} beyond the accepted baseline")
        for line in extra:
            print("  " + line)
        bad |= bool(extra)
    if not args.no_audit:
        vulns, why = run_pip_audit()
        if vulns is None:
            print(f"dependency audit: NOT RUN ({why})")
            bad = 1
        else:
            print(f"dependency audit: {len(vulns)} package(s) with advisories")
            for line in vulns:
                print("  " + line)
            bad |= bool(vulns)
    return int(bool(bad))


if __name__ == "__main__":
    raise SystemExit(main())
