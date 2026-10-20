"""Dependency and CVE radar (650) against OSV.dev. Only package names, versions and ecosystems are sent (never code, paths,
keys or file contents); answers are cached for a day (advisory text for a week) and requests are spaced at least two seconds
apart, in batches, with a hard cap per run."""
from __future__ import annotations

import asyncio
import json
import re
import time
from minty_core._toml import tomllib
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable

QUERY_URL = "https://api.osv.dev/v1/querybatch"
VULN_URL = "https://api.osv.dev/v1/vulns/"
BATCH, MAX_REQUESTS, GAP_S = 200, 30, 2.0
Q_TTL, V_TTL = 86400.0, 7 * 86400.0
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?==([A-Za-z0-9_.+!-]+)")


def parse_lockfiles(path: Path) -> list[tuple[str, str, str]]:
    path, found = Path(path), set()
    req = path / "requirements.txt"
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _REQ.match(line.split("#", 1)[0].strip())
            if m:
                found.add(("PyPI", m.group(1).lower(), m.group(2)))
    lock = path / "package-lock.json"
    if lock.is_file():
        try:
            pkgs = json.loads(lock.read_text(encoding="utf-8")).get("packages", {})
        except ValueError:
            pkgs = {}
        for key, meta in pkgs.items():
            if "node_modules/" in key and meta.get("version"):
                found.add(("npm", key.rsplit("node_modules/", 1)[1], meta["version"]))
    for name, eco in (("Cargo.lock", "crates.io"), ("poetry.lock", "PyPI"), ("uv.lock", "PyPI")):
        f = path / name
        if f.is_file():
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError:
                continue
            for p in data.get("package", []):
                if p.get("name") and p.get("version") and not p.get("source", {}) == {"virtual": "."}:
                    found.add((eco, str(p["name"]).lower() if eco == "PyPI" else str(p["name"]), str(p["version"])))
    return sorted(found)[:2000]


class Osv:
    def __init__(self, post: Callable[[str, dict], Awaitable[tuple[int, dict]]],
                 get: Callable[[str], Awaitable[tuple[int, dict]]], cache: Path, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._post, self._get, self._cache_path, self._clock, self._sleep = post, get, Path(cache), clock, sleep
        try:
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._cache = {}
        self._cache.setdefault("q", {})
        self._cache.setdefault("v", {})
        self._requests = 0

    def _save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

    async def _spaced(self) -> bool:
        if self._requests >= MAX_REQUESTS:
            return False
        if self._requests:
            await self._sleep(GAP_S)
        self._requests += 1
        return True

    async def query(self, packages) -> tuple[dict, bool]:
        """({(eco, name, version): [ids]}, complete?) using the cache first."""
        now, result, todo = self._clock(), {}, []
        for p in packages:
            hit = self._cache["q"].get("|".join(p))
            if hit and now - hit["at"] < Q_TTL:
                result[p] = hit["ids"]
            else:
                todo.append(p)
        complete = True
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            if not await self._spaced():
                complete = False
                break
            body = {"queries": [{"package": {"name": n, "ecosystem": e}, "version": v} for e, n, v in chunk]}
            status, data = await self._post(QUERY_URL, body)
            if status != 200:
                complete = False
                break
            for p, r in zip(chunk, data.get("results", [])):
                ids = [v["id"] for v in (r or {}).get("vulns", []) if v.get("id")]
                result[p] = ids
                self._cache["q"]["|".join(p)] = {"ids": ids, "at": now}
        self._save()
        return result, complete

    async def detail(self, vuln_id: str) -> dict:
        now = self._clock()
        hit = self._cache["v"].get(vuln_id)
        if hit and now - hit["at"] < V_TTL:
            return hit["d"]
        if not await self._spaced():
            return {}
        status, data = await self._get(VULN_URL + vuln_id)
        if status != 200:
            return {}
        fixed = [e["fixed"] for a in data.get("affected", []) for r in a.get("ranges", []) for e in r.get("events", [])
                 if "fixed" in e]
        d = {"summary": str(data.get("summary") or "")[:200], "severity": str(
            (data.get("database_specific") or {}).get("severity") or ""), "fixed": fixed[0] if fixed else ""}
        self._cache["v"][vuln_id] = {"d": d, "at": now}
        self._save()
        return d


async def radar(project: Path, osv: Osv) -> str:
    packages = parse_lockfiles(project)
    if not packages:
        return ("No pinned packages found (requirements.txt with ==, package-lock.json, Cargo.lock, poetry.lock, uv.lock), "
                "so there is nothing to check.")
    found, complete = await osv.query(packages)
    eco = Counter(p[0] for p in packages)
    head = f"Checked {len(packages)} pinned packages ({', '.join(f'{k} {v}' for k, v in eco.most_common())})."
    if not complete and not found:
        return head + " The OSV service could not be checked just now; try again later."
    bad = [(p, ids) for p, ids in found.items() if ids]
    lines = [head]
    if not bad:
        lines.append("None of them has a known problem in the OSV database."
                     + ("" if complete else " (Some could not be checked this time.)"))
        return "\n".join(lines)
    lines.append(f"{len(bad)} {'has' if len(bad) == 1 else 'have'} known problems:")
    for (eco_, name, ver), ids in sorted(bad)[:5]:
        d = await osv.detail(ids[0])
        extra = f" {d['severity']}" if d.get("severity") else ""
        fix = f" (fixed in {d['fixed']})" if d.get("fixed") else ""
        more = f" and {len(ids) - 1} more" if len(ids) > 1 else ""
        lines.append(f"{name} {ver}: {ids[0]}{extra}{more} — {d.get('summary', 'no summary')[:90]}{fix}")
    if len(bad) > 5:
        lines.append(f"…and {len(bad) - 5} more packages.")
    if not complete:
        lines.append("(Some could not be checked this time.)")
    return "\n".join(lines)
