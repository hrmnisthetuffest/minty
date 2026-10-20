"""The release workflow: a pushed version tag becomes the GitHub release the updater looks for."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_release_runs_on_version_tags_only_and_uses_nothing_but_the_built_in_token():
    flow = workflow("release.yml")
    triggers = flow.get("on") or flow.get(True)  # YAML reads a bare `on` as true
    assert triggers == {"push": {"tags": ["v*"]}}
    assert flow["permissions"] == {"contents": "write"}
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert text.count("secrets.") == 1 and "secrets.GITHUB_TOKEN" in text
    steps = " ".join(str(s.get("run", "")) for s in flow["jobs"]["release"]["steps"])
    assert "scripts/release.py" in steps and "release_tools.py notes" in steps and "gh release create" in steps
    assert "--prerelease" in steps  # alpha, beta and rc tags are pre-releases; a plain 1.0.1 is not


def test_tests_run_on_pushes_and_pull_requests():
    flow = workflow("ci.yml")
    triggers = flow.get("on") or flow.get(True)
    assert "pull_request" in triggers and "push" in triggers
