"""One version everywhere: the package, the packaging metadata and the changelog agree.

Before the first release the version is 1.0.0-alpha.N. From the first release on it is a plain MAJOR.MINOR.PATCH (1.0.1), changed
only when a release is made (scripts/release_tools.py): day-to-day changes are logged under "Unreleased" in CHANGELOG.md."""
import re
import tomllib
from pathlib import Path

from minty_core import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_and_the_packaging_metadata_have_the_same_version():
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?", __version__)
    assert m, f"{__version__!r} should look like 1.0.1 (or 1.0.0-alpha.3 before the first release)"
    major, minor, patch, stage, n = m.groups()
    short = {"alpha": "a", "beta": "b", "rc": "rc"}.get(stage, "")
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["version"] == f"{major}.{minor}.{patch}{short}{n or ''}"


def test_the_changelog_has_a_heading_for_the_current_version():
    headings = [line for line in (ROOT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## ")]
    assert any(h.startswith(f"## {__version__}") for h in headings), headings


def test_unreleased_changes_are_logged_in_one_section_at_the_bottom():
    headings = [line for line in (ROOT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## ")]
    assert headings.count("## Unreleased") <= 1
    if "## Unreleased" in headings:
        assert headings[-1] == "## Unreleased"
