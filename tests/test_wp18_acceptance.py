"""WP-18 acceptance (plan-master):
* the test runner returns a five-line-or-shorter failure summary for a seeded failing suite;
* OSV lookups are cached and rate-limited, and no code or secrets are uploaded;
* the plugin template produces a plugin that passes the validator and loads; and a dangerous edit of it does not pass."""
import shutil
import subprocess
from pathlib import Path

import pytest

from minty_core.dev.plugin_kit import plugin_docs, scaffold, validate
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR
from tests.test_dev_plugin import Rig
from tests.test_dev_tests_ci_osv import FAILING


def git_project(root, name):
    p = root / name
    p.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=p, check=True, capture_output=True)
    return p


@pytest.mark.skipif(shutil.which("pytest") is None, reason="pytest binary not on PATH")
async def test_a_seeded_failing_suite_gives_at_most_five_lines_through_the_tool(tmp_path):
    root = tmp_path / "projects"
    p = git_project(root, "seeded")
    (p / "tests").mkdir()
    (p / "tests" / "test_a.py").write_text(FAILING)
    (p / "pyproject.toml").write_text('[project]\nname = "seeded"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    r = Rig(tmp_path, root)
    del r.host._services["dev_test_runner"]  # the real runner, the real pytest
    r.host._services["dev"]._test_runner = None
    out = await r.call("repo", {"action": "tests", "project": "seeded"})
    body = out.splitlines()[1:]  # the first line is the untrusted-text tag
    assert 2 <= len(body) <= 5 and body[0].startswith("2 failed, 3 passed")
    assert "test_bad_math" in out and "test_bad_text" in out


async def test_osv_gets_names_and_versions_only_and_a_second_run_is_free(tmp_path):
    root = tmp_path / "projects"
    p = git_project(root, "app")
    (p / "requirements.txt").write_text("requests==2.19.0\n")
    (p / "secrets.py").write_text("API_KEY = 'gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF'\n")
    (p / "app.py").write_text("print('private business logic')\n")
    r = Rig(tmp_path, root, osv=True)
    first = await r.call("repo", {"action": "deps", "project": "app"})
    second = await r.call("repo", {"action": "deps", "project": "app"})
    assert "requests 2.19.0" in first and first == second
    assert len(r.osv.posts) == 1 and len(r.osv.gets) == 1  # the second run came from the cache
    sent = repr(r.osv.posts) + repr(r.osv.gets)
    assert "gsk_" not in sent and "private business logic" not in sent and str(p) not in sent
    (url, body), = r.osv.posts
    assert set(body["queries"][0]) == {"package", "version"} and set(body["queries"][0]["package"]) == {"name", "ecosystem"}


def test_the_template_passes_and_a_dangerous_edit_of_it_does_not(tmp_path):
    scaffold("greeter", tmp_path / "plugins", "Greets people.")
    folder = tmp_path / "plugins" / "greeter"
    assert validate(folder) == []
    code = (folder / "plugin.py").read_text()
    (folder / "plugin.py").write_text(code.replace('who = str(args.get("who") or "world")[:40]',
                                                    'who = eval(args["who"])'))
    assert any(p.level == "error" and "eval" in p.message for p in validate(folder))


def test_the_committed_plugin_docs_are_current():
    docs = Path(__file__).resolve().parents[1] / "docs" / "plugins.md"
    assert docs.read_text(encoding="utf-8") == plugin_docs([BUILTIN_PLUGIN_DIR]), (
        "run: .venv/bin/python scripts/gen_plugin_docs.py")
