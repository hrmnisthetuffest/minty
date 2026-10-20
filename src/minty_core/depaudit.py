"""Known-vulnerability check for a package, before it is installed (OSV.dev, free, no key, no new dependency).

The result is a sentence for the confirmation question: the person decides with the facts in front of them. If the
network is down it says so instead of staying quiet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from minty_core.httpclient import get_client

OSV_URL = "https://api.osv.dev/v1/query"
PYPI_URL = "https://pypi.org/pypi/{name}/json"
NPM_URL = "https://registry.npmjs.org/{name}/latest"
TIMEOUT_S = 6.0


@dataclass
class Audit:
    checked: bool
    package: str
    version: str = ""
    vulns: list[tuple[str, str]] = field(default_factory=list)  # (id, summary)
    error: str = ""


def parse_spec(manager: str, package: str) -> tuple[str, str, str | None]:
    """(ecosystem, name, pinned version or None)."""
    package = str(package or "").strip()
    if manager == "npm":
        body = package[1:] if package.startswith("@") else package
        name, _, version = body.partition("@")
        return "npm", ("@" + name if package.startswith("@") else name), (version or None)
    name = re.split(r"[=<>!~\[;@ ]", package, maxsplit=1)[0]
    version = package.split("==", 1)[1].split(",")[0].strip() if "==" in package else None
    return "PyPI", name, version


async def audit(manager: str, package: str, client: httpx.AsyncClient | None = None) -> Audit:
    ecosystem, name, version = parse_spec(manager, package)
    if not name:
        return Audit(False, package, error="no package name")
    client = client or get_client()
    try:
        if version is None:  # the version pip or npm would pick: the newest
            url = (PYPI_URL if ecosystem == "PyPI" else NPM_URL).format(name=name)
            resp = await client.get(url, timeout=TIMEOUT_S)
            if resp.status_code != 200:
                return Audit(False, name, error=f"the package index answered {resp.status_code}")
            body = resp.json()
            version = str((body.get("info") or {}).get("version") if ecosystem == "PyPI" else body.get("version"))
        resp = await client.post(OSV_URL, json={"package": {"name": name, "ecosystem": ecosystem},
                                                "version": version}, timeout=TIMEOUT_S)
        if resp.status_code != 200:
            return Audit(False, name, version, error=f"OSV.dev answered {resp.status_code}")
        vulns = [(str(v.get("id", "?")), str(v.get("summary") or v.get("details") or "")[:120])
                 for v in (resp.json().get("vulns") or [])]
        return Audit(True, name, version, vulns)
    except (httpx.HTTPError, ValueError, KeyError, AttributeError) as e:
        return Audit(False, name, version or "", error=type(e).__name__)


def note(a: Audit) -> str:
    """The sentence added to the confirmation question."""
    shown = f"{a.package} {a.version}".strip()
    if not a.checked:
        return f"I couldn't check {shown} for known vulnerabilities ({a.error or 'no answer'})."
    if not a.vulns:
        return f"OSV.dev lists no known vulnerabilities for {shown}."
    ids = ", ".join(i for i, _ in a.vulns[:3])
    first = f" The first: {a.vulns[0][1]}." if a.vulns[0][1] else ""
    n = len(a.vulns)
    return f"OSV.dev lists {n} known vulnerabilit{'ies' if n != 1 else 'y'} for {shown} ({ids}).{first}"
