"""Updating Minty from its GitHub repository: a check that tells you, and an update that a failed check rolls back."""
import json
import subprocess
from pathlib import Path

import pytest

from minty_core.config import Config
from minty_core.selfupdate import (Release, detect_repo, UpdateResult, apply_update, check_due, latest_release, newer, origin_matches,
                                   parse_version, record_check, run_cli)


# ---- versions ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, parsed", [
    ("1.0.0-alpha.19", (1, 0, 0, 0, 19)), ("1.0.0a19", (1, 0, 0, 0, 19)), ("v1.0.0-alpha.2", (1, 0, 0, 0, 2)),
    ("1.0.0-beta.1", (1, 0, 0, 1, 1)), ("1.0.0rc2", (1, 0, 0, 2, 2)), ("v1.0.0", (1, 0, 0, 3, 0)), ("1.2.3", (1, 2, 3, 3, 0)),
])
def test_versions_are_read_in_every_spelling(text, parsed):
    assert parse_version(text) == parsed


@pytest.mark.parametrize("text", ["", "latest", "1.0", "nightly-2026", "v1.0.0-banana.1"])
def test_other_tags_are_not_versions(text):
    assert parse_version(text) is None


def test_alpha_comes_before_beta_before_release():
    order = ["1.0.0-alpha.2", "1.0.0-alpha.10", "1.0.0-beta.1", "1.0.0-rc.1", "1.0.0", "1.0.1-alpha.1", "1.1.0-alpha.1"]
    assert sorted(order, key=parse_version) == order


def test_newer_compares_the_installed_version_with_a_tag():
    assert newer("1.0.0-alpha.19", "v1.0.0-alpha.20") and not newer("1.0.0-alpha.19", "v1.0.0-alpha.19")
    assert not newer("1.0.0-alpha.19", "v1.0.0-alpha.3") and not newer("1.0.0-alpha.19", "nightly")
    assert newer("1.0.0-alpha.19", "v1.0.0")


# ---- asking GitHub -------------------------------------------------------------------------------------------
RELEASES = [
    {"tag_name": "v1.0.0-alpha.20", "draft": False, "prerelease": True, "html_url": "https://github.com/o/minty/releases/tag/v1.0.0-alpha.20",
     "body": "Hotkeys and updates."},
    {"tag_name": "v1.0.0-alpha.22", "draft": True, "prerelease": True, "html_url": "x", "body": "unfinished"},
    {"tag_name": "v1.0.0-alpha.21", "draft": False, "prerelease": True, "html_url": "https://github.com/o/minty/releases/tag/v1.0.0-alpha.21",
     "body": "A" * 3000},
    {"tag_name": "nightly", "draft": False, "prerelease": True, "html_url": "y", "body": ""},
]


async def test_the_newest_published_release_wins_prereleases_included_drafts_not():
    calls = []

    async def get(url):
        calls.append(url)
        return 200, RELEASES

    rel = await latest_release("o/minty", get)
    assert calls == ["https://api.github.com/repos/o/minty/releases?per_page=30"]
    assert rel.tag == "v1.0.0-alpha.21" and rel.url.endswith("alpha.21") and len(rel.notes) <= 600


@pytest.mark.parametrize("status, data", [(404, {"message": "Not Found"}), (403, {}), (200, []), (200, "oops"), (200, [{"tag_name": 5}])])
async def test_no_release_or_a_failed_request_is_just_none(status, data):
    async def get(url):
        return status, data

    assert await latest_release("o/minty", get) is None


async def test_a_network_error_is_none_not_a_crash():
    async def get(url):
        raise OSError("offline")

    assert await latest_release("o/minty", get) is None


@pytest.mark.parametrize("repo", ["", "nobody", "a/b/c", "../x", "o/minty;rm -rf", "https://github.com/o/minty"])
async def test_only_owner_slash_name_is_a_repository(repo):
    async def get(url):
        raise AssertionError("must not ask")

    assert await latest_release(repo, get) is None


# ---- how often ----------------------------------------------------------------------------------------------
def test_a_check_happens_once_a_day(tmp_path):
    assert check_due(tmp_path, now=1000.0, every_hours=24)
    record_check(tmp_path, now=1000.0, tag="v1.0.0-alpha.21")
    assert not check_due(tmp_path, now=1000.0 + 3600, every_hours=24)
    assert check_due(tmp_path, now=1000.0 + 24 * 3600 + 1, every_hours=24)


def test_a_damaged_state_file_just_means_check_again(tmp_path):
    (tmp_path / "update-state").mkdir()
    (tmp_path / "update-state" / "last-check.json").write_text("{not json")
    assert check_due(tmp_path, now=1.0, every_hours=24)


# ---- the origin ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url, ok", [
    ("https://github.com/o/minty.git", True), ("https://github.com/o/minty", True), ("git@github.com:o/minty.git", True),
    ("https://github.com/evil/minty.git", False), ("https://evil.example/o/minty.git", False), ("https://github.com/o/minty-fork.git", False),
])
def test_only_the_configured_repository_counts_as_the_origin(url, ok):
    assert origin_matches(url, "o/minty") is ok


# ---- a real update in a real git repository -------------------------------------------------------------------
def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                                "GIT_COMMITTER_EMAIL": "t@example.com", "HOME": str(cwd), "PATH": "/usr/bin:/bin"}).stdout


def commit(cwd, version, deps="x"):
    (cwd / "src" / "minty_core").mkdir(parents=True, exist_ok=True)
    (cwd / "src" / "minty_core" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (cwd / "pyproject.toml").write_text(f'[project]\nname = "minty-core"\ndependencies = ["{deps}"]\n')
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", version)
    git(cwd, "tag", f"v{version}")


class Runner:
    """Real git, a pretend Python: records the pip and version commands, and can be told the new version does not start."""

    def __init__(self, broken=False, root=None):
        self.python, self.broken, self.root = [], broken, root

    def __call__(self, argv, cwd):
        if argv[0] == "git":
            done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                  env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                                       "GIT_COMMITTER_EMAIL": "t@example.com", "HOME": str(cwd), "PATH": "/usr/bin:/bin"})
            return done.returncode, done.stdout + done.stderr
        self.python.append(argv[1:])
        if "--version" in argv:
            version = (self.root / "src" / "minty_core" / "__init__.py").read_text().split('"')[1]
            return (1, "Traceback: boom") if self.broken and "alpha.2" in version else (0, f"Minty {version}")
        return 0, ""


@pytest.fixture
def repos(tmp_path):
    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, "1.0.0-alpha.1")
    git(tmp_path, "clone", "-q", str(origin), str(work))
    commit(origin, "1.0.0-alpha.2", deps="y")
    return origin, work


def release(tag="v1.0.0-alpha.2"):
    return Release(tag=tag, url="u", notes="n")


def apply(work, tmp_path, runner=None, tag="v1.0.0-alpha.2", **kw):
    return apply_update(work, tmp_path / "cfg", release(tag), "o/minty", runner=runner or Runner(root=work), python="py",
                        origin_ok=lambda url: True, **kw)


def head(work):
    return git(work, "rev-parse", "HEAD").strip()


def test_an_update_moves_to_the_new_tag_installs_changed_dependencies_and_checks_it_starts(repos, tmp_path):
    origin, work = repos
    before, runner = head(work), Runner(root=work)
    result = apply(work, tmp_path, runner)
    assert result.ok and result.new == "v1.0.0-alpha.2" and head(work) != before
    assert "alpha.2" in (work / "src" / "minty_core" / "__init__.py").read_text()
    assert ["-m", "pip", "install", "-q", "-e", "."] in runner.python and ["-m", "minty_core", "--version"] in runner.python


def test_dependencies_are_only_installed_when_they_changed(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    commit(origin, "1.0.0-alpha.1")
    git(tmp_path, "clone", "-q", str(origin), str(tmp_path / "work"))
    commit(origin, "1.0.0-alpha.2")  # the same dependencies
    runner = Runner(root=tmp_path / "work")
    assert apply(tmp_path / "work", tmp_path, runner).ok
    assert not any("pip" in a for a in runner.python)


def test_local_changes_stop_the_update_and_nothing_is_touched(repos, tmp_path):
    _, work = repos
    (work / "notes.txt").write_text("mine")
    git(work, "add", "notes.txt")
    before = head(work)
    result = apply(work, tmp_path)
    assert not result.ok and "local changes" in result.message and head(work) == before


def test_a_checkout_that_has_gone_its_own_way_is_not_overwritten(repos, tmp_path):
    _, work = repos
    commit(work, "1.0.0-mine")
    before = head(work)
    result = apply(work, tmp_path)
    assert not result.ok and "own changes" in result.message and head(work) == before


def test_a_failed_start_rolls_back_to_the_commit_that_worked(repos, tmp_path):
    _, work = repos
    before = head(work)
    result = apply(work, tmp_path, Runner(broken=True, root=work))
    assert not result.ok and result.rolled_back and head(work) == before
    assert "alpha.1" in (work / "src" / "minty_core" / "__init__.py").read_text()


def test_an_unknown_tag_or_a_foreign_origin_is_refused(repos, tmp_path):
    _, work = repos
    before = head(work)
    assert not apply(work, tmp_path, tag="v9.9.9").ok
    result = apply_update(work, tmp_path / "cfg", release(), "o/minty", runner=Runner(root=work), python="py",
                          origin_ok=lambda url: False)
    assert not result.ok and "origin" in result.message and head(work) == before


def test_something_that_is_not_a_git_checkout_is_told_how_to_update(tmp_path):
    result = apply_update(tmp_path, tmp_path / "cfg", release(), "o/minty", runner=Runner(root=tmp_path), python="py")
    assert not result.ok and "install.sh" in result.message


def test_the_last_good_commit_is_recorded_for_a_manual_rollback(repos, tmp_path):
    _, work = repos
    before = head(work)
    apply(work, tmp_path)
    state = json.loads((tmp_path / "cfg" / "update-state" / "last-good.json").read_text())
    assert state["commit"] == before


# ---- the settings and the command ---------------------------------------------------------------------------------
def test_the_settings_default_to_checking_and_telling_never_installing_by_itself():
    c = Config().update
    assert c.repo == "" and c.check is True and c.auto_install is False and c.check_every_hours == 24


async def test_the_command_reports_without_installing(tmp_path, capsys):
    async def get(url):
        return 200, RELEASES

    code = await run_cli(["--check"], root=tmp_path, config_dir=tmp_path / "cfg", repo="o/minty", get=get, current="1.0.0-alpha.19")
    out = capsys.readouterr().out
    assert code == 10 and "1.0.0-alpha.21" in out and "minty update" in out
    code = await run_cli(["--check"], root=tmp_path, config_dir=tmp_path / "cfg", repo="o/minty", get=get, current="1.0.0-alpha.21")
    assert code == 0 and "up to date" in capsys.readouterr().out


async def test_the_command_needs_a_repository(tmp_path, capsys):
    async def get(url):
        raise AssertionError

    code = await run_cli(["--check"], root=tmp_path, config_dir=tmp_path, repo="", get=get, current="1.0.0-alpha.19")
    assert code == 2 and "update.repo" in capsys.readouterr().out


async def test_an_update_asks_first_unless_told_not_to(repos, tmp_path, capsys):
    _, work = repos

    async def get(url):
        return 200, [{"tag_name": "v1.0.0-alpha.2", "draft": False, "prerelease": True, "html_url": "u", "body": "notes"}]

    before = head(work)
    code = await run_cli([], root=work, config_dir=tmp_path / "cfg", repo="o/minty", get=get, current="1.0.0-alpha.1",
                         ask=lambda prompt: False, runner=Runner(root=work), origin_ok=lambda url: True)
    assert code == 1 and head(work) == before and "notes" in capsys.readouterr().out
    code = await run_cli(["--yes"], root=work, config_dir=tmp_path / "cfg", repo="o/minty", get=get, current="1.0.0-alpha.1",
                         runner=Runner(root=work), origin_ok=lambda url: True)
    assert code == 0 and head(work) != before


# ---- the daily check ----------------------------------------------------------------------------------------------
from minty_core.config import UpdateConfig  # noqa: E402
from minty_core.selfupdate import background_check  # noqa: E402


class Notices:
    def __init__(self):
        self.items = []

    def __call__(self, kind, **fields):
        self.items.append((kind, fields))


async def releases_get(url):
    return 200, RELEASES


async def test_it_does_nothing_until_a_repository_is_set(tmp_path):
    async def get(url):
        raise AssertionError("must not ask")

    notices = Notices()
    assert await background_check(UpdateConfig(), tmp_path, notices, root=tmp_path, get=get, current="1.0.0-alpha.19") is None
    assert await background_check(UpdateConfig(repo="o/minty", check=False), tmp_path, notices, root=tmp_path, get=get) is None
    assert notices.items == []


async def test_a_newer_release_is_announced_once_on_the_display(tmp_path):
    notices, cfg = Notices(), UpdateConfig(repo="o/minty")
    rel = await background_check(cfg, tmp_path, notices, root=tmp_path, now=1000.0, get=releases_get, current="1.0.0-alpha.19")
    assert rel.tag == "v1.0.0-alpha.21"
    assert notices.items == [("notice", {"message": "Minty 1.0.0-alpha.21 is available. Run `minty update` to install it."})]
    # a day later it is still there, and nobody is told again
    assert await background_check(cfg, tmp_path, notices, root=tmp_path, now=1000.0 + 25 * 3600, get=releases_get,
                                  current="1.0.0-alpha.19") is None
    assert len(notices.items) == 1


async def test_it_asks_at_most_once_a_day(tmp_path):
    asked = []

    async def get(url):
        asked.append(url)
        return 200, RELEASES

    cfg, notices = UpdateConfig(repo="o/minty"), Notices()
    await background_check(cfg, tmp_path, notices, root=tmp_path, now=1000.0, get=get, current="1.0.0-alpha.21")
    await background_check(cfg, tmp_path, notices, root=tmp_path, now=2000.0, get=get, current="1.0.0-alpha.21")
    assert len(asked) == 1 and notices.items == []


async def test_a_new_release_after_the_old_one_is_announced_again(tmp_path):
    notices, cfg = Notices(), UpdateConfig(repo="o/minty")
    await background_check(cfg, tmp_path, notices, root=tmp_path, now=0.0, get=releases_get, current="1.0.0-alpha.19")

    async def later(url):
        return 200, [{"tag_name": "v1.0.0-alpha.30", "draft": False, "prerelease": True, "html_url": "", "body": ""}]

    await background_check(cfg, tmp_path, notices, root=tmp_path, now=30 * 3600.0, get=later, current="1.0.0-alpha.19")
    assert [n[1]["message"][:19] for n in notices.items] == ["Minty 1.0.0-alpha.2", "Minty 1.0.0-alpha.3"]


async def test_auto_install_installs_and_says_what_happened(tmp_path):
    seen = []

    def fake_apply(root, config_dir, release, repo, runner, python, origin_ok, verify_signed):
        seen.append((release.tag, repo, verify_signed))
        return UpdateResult(True, f"Updated to {release.tag}. It starts using it the next time Minty restarts.")

    notices = Notices()
    cfg = UpdateConfig(repo="o/minty", auto_install=True, verify_signed=True)
    await background_check(cfg, tmp_path, notices, root=tmp_path, now=1.0, get=releases_get, current="1.0.0-alpha.19", apply=fake_apply)
    assert seen == [("v1.0.0-alpha.21", "o/minty", True)]
    assert notices.items == [("notice", {"message": "Updated to v1.0.0-alpha.21. It starts using it the next time Minty restarts."})]


def test_a_repository_name_is_checked_when_it_is_saved():
    from pydantic import ValidationError

    assert UpdateConfig(repo="someone/minty").repo == "someone/minty"
    for bad in ("minty", "a/b/c", "https://github.com/a/b", "a b/c"):
        with pytest.raises(ValidationError):
            UpdateConfig(repo=bad)


@pytest.mark.parametrize("url, repo", [
    ("https://github.com/someone/minty.git", "someone/minty"), ("git@github.com:someone/minty.git", "someone/minty"),
    ("https://github.com/someone/minty", "someone/minty"), ("https://gitlab.com/someone/minty.git", ""), ("/tmp/local/repo", ""),
])
def test_a_clone_knows_its_repository_from_its_origin(tmp_path, url, repo):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "remote", "add", "origin", url)
    assert detect_repo(tmp_path, Runner(root=tmp_path)) == repo


def test_no_repository_without_a_git_checkout(tmp_path):
    assert detect_repo(tmp_path) == ""


def test_the_release_script_checks_the_tag_against_the_version_and_the_changelog(tmp_path):
    import importlib.util
    import sys as _sys

    from minty_core import __version__

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("release_script", root / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clean = [p for p in module.problems(root, f"v{__version__}") if "uncommitted" not in p and "already exists" not in p]
    assert clean == []
    assert any("does not match" in p for p in module.problems(root, "v9.9.9"))
    assert any("not a version" in p for p in module.problems(root, "nightly"))
