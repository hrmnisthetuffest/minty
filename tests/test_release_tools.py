"""The maintainer's release helpers: which number comes next, the changelog's Unreleased section, release notes, and the leak check."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tools = load("release_tools")
leaks = load("leak_check")


# ---- version numbers ---------------------------------------------------------------------------------
@pytest.mark.parametrize("current, bump, expected", [
    ("1.0.1", "patch", "1.0.2"), ("1.0.9", "patch", "1.0.10"), ("1.4.7", "minor", "1.5.0"), ("1.4.7", "major", "2.0.0"),
    ("1.0.0-alpha.27", "first", "1.0.1"), ("1.0.0", "first", "1.0.1"),
])
def test_next_version(current, bump, expected):
    assert tools.next_version(current, bump) == expected


def test_a_prerelease_cannot_take_an_ordinary_bump_and_unknown_bumps_are_refused():
    for bad in ("patch", "minor", "major"):
        with pytest.raises(ValueError, match="first"):
            tools.next_version("1.0.0-alpha.27", bad)
    with pytest.raises(ValueError):
        tools.next_version("1.0.1", "sideways")
    with pytest.raises(ValueError):
        tools.next_version("banana", "patch")
    with pytest.raises(ValueError, match="first"):
        tools.next_version("1.0.1", "first")  # the first release happens once


def test_set_version_writes_the_package_and_the_packaging_metadata(tmp_path):
    (tmp_path / "src" / "minty_core").mkdir(parents=True)
    (tmp_path / "src" / "minty_core" / "__init__.py").write_text('"""x"""\n__version__ = "1.0.0-alpha.27"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "minty-core"\nversion = "1.0.0a27"\nother = "1.0.0a27"\n')
    tools.set_version(tmp_path, "1.0.1")
    assert '__version__ = "1.0.1"' in (tmp_path / "src/minty_core/__init__.py").read_text()
    meta = (tmp_path / "pyproject.toml").read_text()
    assert 'version = "1.0.1"' in meta and 'other = "1.0.0a27"' in meta  # only the project version line


# ---- changelog ---------------------------------------------------------------------------------------
CHANGELOG = "# Changelog\n\n## 1.0.0-alpha.27 (2026-10-20)\n\n- old\n\n## Unreleased\n\n### Faster\n- a new thing\n"


def test_finalize_turns_unreleased_into_the_version_and_its_date():
    out = tools.finalize_changelog(CHANGELOG, "1.0.1", "2026-11-01")
    assert "## 1.0.1 (2026-11-01)" in out and "## Unreleased" not in out and out.index("old") < out.index("a new thing")
    assert out.endswith("- a new thing\n")


def test_finalize_refuses_an_empty_or_missing_unreleased_section():
    with pytest.raises(ValueError, match="nothing to release"):
        tools.finalize_changelog("# Changelog\n\n## 1.0.1 (x)\n\n- a\n", "1.0.2", "2026-11-01")
    with pytest.raises(ValueError, match="nothing to release"):
        tools.finalize_changelog("# Changelog\n\n## Unreleased\n\n", "1.0.2", "2026-11-01")


def test_add_entry_creates_unreleased_at_the_bottom_and_then_appends_to_it():
    text = tools.add_entry("# Changelog\n\n## 1.0.1 (2026-11-01)\n\n- a\n", "- b")
    assert text.rstrip().endswith("## Unreleased\n\n- b")
    text = tools.add_entry(text, "- c")
    assert text.count("## Unreleased") == 1 and text.rstrip().endswith("- b\n- c")


def test_release_notes_are_the_section_of_that_version():
    log = "# Changelog\n\n## 1.0.1 (2026-11-01)\n\n- one\n\n## 1.0.2 (2026-11-02)\n\n- two\n- three\n"
    assert tools.release_notes(log, "1.0.2") == "- two\n- three"
    assert tools.release_notes(log, "v1.0.1") == "- one"
    with pytest.raises(ValueError):
        tools.release_notes(log, "9.9.9")


def test_this_repositorys_changelog_has_notes_for_its_current_version():
    from minty_core import __version__
    root = Path(__file__).parents[1]
    assert tools.release_notes((root / "CHANGELOG.md").read_text(), __version__)


# ---- the leak check ----------------------------------------------------------------------------------
def make_repo(tmp_path, files: dict[str, str]):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_a_clean_tree_has_no_leaks(tmp_path):
    repo = make_repo(tmp_path, {"README.md": "hello", "src/a.py": "x = 1\n"})
    assert leaks.find_leaks(repo) == []


def test_a_key_shaped_string_outside_tests_is_found_and_never_printed(tmp_path):
    secret = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    repo = make_repo(tmp_path, {"src/a.py": f'KEY = "{secret}"\n', "tests/t.py": f'FAKE = "{secret}"\n'})
    found = leaks.find_leaks(repo)
    assert len(found) == 1 and found[0].startswith("src/a.py") and secret not in "".join(found)  # test fixtures are fake by rule


def test_known_values_and_personal_words_are_found_everywhere_including_tests(tmp_path):
    repo = make_repo(tmp_path, {"tests/t.py": "owner = 'Zed Quux'\nkey = 'my-real-key-value-123'\n", "docs/a.md": "Ask ZED quux\n"})
    found = leaks.find_leaks(repo, known_values=["my-real-key-value-123"], words=["zed quux"])
    joined = "\n".join(found)
    assert "tests/t.py" in joined and "docs/a.md" in joined and "my-real-key-value-123" not in joined
    assert any("personal word" in f for f in found) and any("stored key" in f for f in found)


def test_untracked_and_ignored_files_are_not_scanned(tmp_path):
    repo = make_repo(tmp_path, {"a.txt": "fine", ".gitignore": "secret.txt\n"})
    (repo / "secret.txt").write_text("gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2")
    assert leaks.find_leaks(repo) == []


# ---- merging two changelogs ----------------------------------------------------------------------------
def test_two_unreleased_sections_become_one_at_the_bottom_without_repeats():
    text = ("# Changelog\n\n## 1.0.1 (2026-11-01)\n\n- a\n\n## Unreleased\n\n- from dev\n- shared line\n\n"
            "## Unreleased\n\n- shared line\n- from a pull request\n")
    out = tools.merge_unreleased(text)
    assert out.count("## Unreleased") == 1 and out.rstrip().endswith("- from a pull request")
    body = out.split("## Unreleased", 1)[1]
    assert body.count("shared line") == 1 and "from dev" in body and out.index("- a") < out.index("## Unreleased")


def test_a_changelog_with_one_or_no_unreleased_section_is_left_alone():
    one = "# Changelog\n\n## 1.0.1 (x)\n\n- a\n\n## Unreleased\n\n- b\n"
    assert tools.merge_unreleased(one) == one
    none = "# Changelog\n\n## 1.0.1 (x)\n\n- a\n"
    assert tools.merge_unreleased(none) == none


def test_changelog_lines_added_on_two_branches_merge_without_a_conflict():
    assert "CHANGELOG.md merge=union" in (Path(__file__).parents[1] / ".gitattributes").read_text()
