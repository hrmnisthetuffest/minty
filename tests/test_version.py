"""One version everywhere: the package, the packaging metadata and the changelog agree. Every change bumps the alpha number."""
import re
import tomllib
from pathlib import Path

from minty_core import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_and_the_packaging_metadata_have_the_same_version():
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)-(alpha|beta|rc)\.(\d+)", __version__)
    assert m, f"{__version__!r} should look like 1.0.0-alpha.3"
    major, minor, patch, stage, n = m.groups()
    short = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["version"] == f"{major}.{minor}.{patch}{short}{n}"


def test_the_changelog_has_a_heading_for_the_current_version():
    headings = [line for line in (ROOT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## ")]
    assert any(h.startswith(f"## {__version__}") for h in headings), headings
