"""requirements.lock pins the core dependencies to exact versions with hashes, so an install from it gets the files that were audited and
nothing newer or swapped. It must cover every direct dependency and respect the floors in pyproject.toml (made by:
`uv pip compile pyproject.toml --generate-hashes --python-version 3.12 --no-header -o requirements.lock`)."""
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def pins():
    out = {}
    for block in re.split(r"\n(?=\S)", (ROOT / "requirements.lock").read_text()):
        m = re.match(r"([A-Za-z0-9_.-]+)==([^\s\\]+)", block)
        if m:
            out[m.group(1).lower().replace("_", "-")] = (m.group(2), block.count("--hash=sha256:"))
    return out


def test_every_pinned_package_has_hashes():
    found = pins()
    assert len(found) >= 15
    assert all(hashes >= 1 for _v, hashes in found.values()), {k: v for k, v in found.items() if v[1] < 1}


def test_every_direct_dependency_is_pinned_at_or_above_its_floor():
    found = pins()
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    for line in deps:
        req = Requirement(line)
        if req.marker is not None and not req.marker.evaluate({"python_version": "3.12"}):
            continue
        name = req.name.lower().replace("_", "-")
        assert name in found, f"{name} is not pinned in requirements.lock"
        assert Version(found[name][0]) in req.specifier, f"{name} {found[name][0]} is outside {req.specifier}"


def test_the_fixed_advisories_stay_fixed():
    assert Version(pins()["certifi"][0]) >= Version("2026.10.18")
