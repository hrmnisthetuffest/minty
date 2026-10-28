"""A coding agent is started with the environment it needs, not Minty's.

Claude Code can run python, pip and node. If it inherits the parent environment, a page or file it reads could talk it into
printing every provider key Minty was started with. It keeps what it needs to run and to sign in, and nothing else.
"""
import sys

import pytest

from minty_core.plugins.builtin.agents import adapters
from minty_core.plugins.builtin.agents.adapters import ClaudeCodeAdapter, Invocation, Parsed
from minty_core.plugins.builtin.agents.jobs import JobManager

LEAKY = {
    "GROQ_API_KEY": "gsk-leak", "OPENAI_API_KEY": "sk-leak", "GOOGLE_API_KEY": "g-leak", "GITHUB_TOKEN": "ghp-leak",
    "TELEGRAM_BOT_TOKEN": "tg-leak", "MINTY_API_TOKEN": "mt-leak", "AWS_SECRET_ACCESS_KEY": "aws-leak",
    "SSH_AUTH_SOCK": "/run/user/1000/agent", "SOME_PASSWORD": "pw-leak", "DATABASE_URL": "postgres://u:pw@h/db",
}
NEEDED = {"PATH": "/usr/bin:/bin", "HOME": "/home/me", "LANG": "C.UTF-8", "ANTHROPIC_API_KEY": "sk-ant-mine",
          "CLAUDE_CONFIG_DIR": "/home/me/.claude", "HTTPS_PROXY": "http://proxy:3128", "SSL_CERT_FILE": "/etc/ssl/c.pem",
          "XDG_CONFIG_HOME": "/home/me/.config", "TERM": "xterm"}


def test_only_what_claude_code_needs_is_passed_on():
    env = adapters.job_env({**LEAKY, **NEEDED})
    assert set(env) == set(NEEDED)
    assert not [k for k in env if k in LEAKY]


def test_the_users_own_extra_names_are_passed_and_nothing_else():
    env = adapters.job_env({**LEAKY, **NEEDED, "AWS_PROFILE": "work"}, extra="AWS_PROFILE")
    assert env["AWS_PROFILE"] == "work" and "AWS_SECRET_ACCESS_KEY" not in env and "GITHUB_TOKEN" not in env


@pytest.mark.parametrize("bad", ["", "  ", "lower case", "A=B", "X;rm", "*"])
def test_a_malformed_extra_name_is_ignored(bad):
    assert adapters.job_env(NEEDED, extra=bad) == adapters.job_env(NEEDED)


class Dump:
    """Prints the environment the job process actually got."""

    name = "fake"
    binary = sys.executable
    readonly_capable = False

    def build(self, project, prompt, session_id, readonly=False):
        return Invocation([sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"], project)

    def parse(self, stdout):
        return Parsed(stdout.strip(), session_id=None)


async def test_a_job_process_does_not_inherit_provider_keys(tmp_path, monkeypatch):
    import asyncio

    for k, v in {**LEAKY, "ANTHROPIC_API_KEY": "sk-ant-mine"}.items():
        monkeypatch.setenv(k, v)
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    mgr = JobManager(root, {"fake": Dump()}, lambda _t: None, state={}, log_dir=tmp_path / "logs",
                     sessions_path=tmp_path / "sessions.json", check=[sys.executable, "-c", "print('1 passed')"],
                     verify_work=False)
    job = await mgr.start("fake", "p", "list the environment")
    await asyncio.wait_for(mgr._tasks[job.id], 20)
    names = job.summary
    assert "ANTHROPIC_API_KEY" in names and "PATH" in names
    for leaked in LEAKY:
        assert leaked not in names, leaked


def test_the_agent_may_not_read_or_write_minty_keys_or_ssh_keys(tmp_path):
    home = tmp_path / "home"
    rules = adapters.secret_path_rules(home=home, config_dir=home / ".config" / "minty")
    joined = " ".join(rules)
    for needle in ("Read(", "Edit(", ".config/minty", ".ssh", ".gnupg"):
        assert needle in joined, needle
    argv = ClaudeCodeAdapter(secret_rules=rules).build(tmp_path, "x", None).argv
    denied = argv[argv.index("--disallowedTools") + 1: argv.index("--allowedTools")]
    assert all(r in denied for r in rules) and "Bash(git push:*)" in denied
