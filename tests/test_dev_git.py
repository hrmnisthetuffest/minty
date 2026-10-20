"""Projects, safe git, the onboarding summary (646) and the daily digest (647), on a real throw-away repository."""
import os
import subprocess

import pytest

from minty_core.dev.digest import digest
from minty_core.dev.git import Git, git_rule, make_runner
from minty_core.dev.onboard import onboarding
from minty_core.dev.projects import Projects


def sh(cwd, *args, date=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Alex", "GIT_AUTHOR_EMAIL": "h@example.com",
           "GIT_COMMITTER_NAME": "Alex", "GIT_COMMITTER_EMAIL": "h@example.com"}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "projects"
    r = root / "demo-app"
    (r / "demo").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "README.md").write_text("# Demo app\n\n[![CI](https://img.shields.io/x.svg)](https://ci.example)\n\n"
                                 "A tiny demo app that counts words.\nIt has a command line.\n\n## Install\nblah\n")
    (r / "pyproject.toml").write_text('[project]\nname = "demo-app"\nrequires-python = ">=3.11"\n'
                                      'dependencies = ["httpx", "rich"]\n[project.scripts]\ndemo = "demo.cli:main"\n'
                                      '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (r / "demo" / "__init__.py").write_text("")
    (r / "demo" / "cli.py").write_text("def main():\n    pass\n")
    (r / "tests" / "test_cli.py").write_text("def test_x():\n    assert True\n")
    sh(r, "git", "init", "-b", "main")
    sh(r, "git", "add", ".")
    sh(r, "git", "commit", "-m", "Initial commit", date="2026-10-10T10:00:00+0530")
    (r / "demo" / "cli.py").write_text("def main():\n    print('hi')\n")
    sh(r, "git", "commit", "-am", "Print a greeting", date="2026-10-18T09:00:00+0530")
    sh(r, "git", "remote", "add", "origin", "https://alex:ghp_SECRET@github.com/alex/demo-app.git")
    return root, r


# ---- projects ---------------------------------------------------------------------------------------------------
def test_projects_are_the_git_folders_under_the_root_and_nothing_outside(repo, tmp_path):
    root, r = repo
    (root / "notes").mkdir()  # not a repo
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, root / "sneaky")
    p = Projects(root, {})
    assert p.names() == ["demo-app"]
    assert p.resolve("Demo-App") == r.resolve()
    for bad in ("notes", "sneaky", "../elsewhere", "", "/etc", "demo-app/../notes"):
        with pytest.raises(ValueError):
            p.resolve(bad)


def test_a_listed_project_outside_the_root_is_allowed_only_by_name(repo, tmp_path):
    root, _ = repo
    other = tmp_path / "other" / "tool"
    other.mkdir(parents=True)
    sh(other, "git", "init", "-b", "main")
    p = Projects(root, {"tool": str(other)})
    assert set(p.names()) == {"demo-app", "tool"} and p.resolve("tool") == other.resolve()
    with pytest.raises(ValueError):
        p.resolve(str(other))


# ---- safe git ----------------------------------------------------------------------------------------------------
async def test_git_can_read_but_never_write():
    runner = make_runner()
    for bad in (["git", "push", "origin", "main"], ["git", "commit", "-m", "x"], ["git", "checkout", "x"],
                ["git", "config", "user.name", "x"], ["git", "branch", "-D", "main"], ["git", "branch", "newbranch"],
                ["git", "diff", "--output=/tmp/x"], ["git", "log", "--output=/tmp/x"], ["git", "diff", "--ext-diff"],
                ["git", "-c", "core.sshCommand=evil", "status"], ["git", "-c", "core.pager=sh -c evil", "log"],
                ["git", "remote", "add", "x", "url"], ["git", "reset", "--hard"], ["git", "clean", "-fdx"]):
        with pytest.raises(ValueError):
            await runner.run(bad)
    for good in (["git", "status", "--porcelain=v1", "-b"], ["git", "log", "-n", "3"], ["git", "branch", "--list"],
                 ["git", "remote", "get-url", "origin"], ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                 ["git", "-c", "core.fsmonitor=false", "status"]):
        assert git_rule(good), good


async def test_the_wrapper_adds_the_safe_config_so_a_repos_own_hooks_do_not_run(repo):
    _root, r = repo
    (r / ".git" / "config").write_text((r / ".git" / "config").read_text() + "[core]\n\tfsmonitor = touch /tmp/minty-pwned\n")
    g = Git(make_runner())
    res = await g.run(r, "status", "--porcelain=v1", "-b")
    assert res.ok and not os.path.exists("/tmp/minty-pwned")


# ---- onboarding ---------------------------------------------------------------------------------------------------
async def test_the_onboarding_summary_says_what_it_is_how_to_run_it_and_what_happened_lately(repo):
    _root, r = repo
    text = await onboarding(r, Git(make_runner()))
    assert text.startswith("demo-app:") and "A tiny demo app that counts words." in text
    assert "shields.io" not in text and "## Install" not in text
    assert "Languages: Python (3 files)" in text
    assert "demo (demo.cli:main)" in text and "To run the tests: pytest" in text
    assert "2 commits" in text and "Print a greeting" in text and "branch main" in text
    assert "ghp_SECRET" not in text and "https://github.com/alex/demo-app.git" in text
    assert len(text) < 1400


async def test_a_folder_that_is_not_a_repository_is_said_plainly(tmp_path):
    (tmp_path / "plain").mkdir()
    assert "not a git repository" in await onboarding(tmp_path / "plain", Git(make_runner()))


# ---- the daily digest ----------------------------------------------------------------------------------------------
async def test_the_digest_lists_commits_since_then_uncommitted_work_and_the_branch_state(repo):
    _root, r = repo
    (r / "demo" / "cli.py").write_text("def main():\n    print('hi')\n    print('more')\n")
    (r / "notes.txt").write_text("todo\n")
    text = await digest(r, Git(make_runner()), since="2026-10-17")
    assert "1 commit" in text and "Print a greeting" in text and "Initial commit" not in text
    assert "Alex" in text and "1 file" in text
    assert "Uncommitted: 1 changed, 1 new" in text
    assert "no upstream" in text


async def test_a_quiet_day_is_said_to_be_quiet(repo):
    _root, r = repo
    text = await digest(r, Git(make_runner()), since="2026-10-19")
    assert "No commits since 2026-10-19" in text and "Nothing uncommitted" in text


async def test_the_since_value_cannot_carry_an_option(repo):
    _root, r = repo
    with pytest.raises(ValueError):
        await digest(r, Git(make_runner()), since="--output=/tmp/x")
    with pytest.raises(ValueError):
        await digest(r, Git(make_runner()), since="yesterday; rm -rf /")
