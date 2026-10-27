from pathlib import Path

from tests.plugin_helpers import make_host


def host(tmp_path, **settings):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return make_host(tmp_path, plugins={"agents": {"enabled": True, "root": str(root), **settings}})


def test_disabled_by_default(tmp_path):
    assert make_host(tmp_path).get_tool("agents_start_task") is None


def test_starting_tools_are_risky_and_reads_are_not(tmp_path):
    h = host(tmp_path)
    assert h.get_tool("agents_start_task").risky is True
    assert h.get_tool("agents_start_project").risky is True
    for name in ("task_status", "stop_task", "list_projects", "list_agents"):
        assert h.get_tool(f"agents_{name}").risky is False


async def test_list_projects_and_agents_and_status(tmp_path, monkeypatch):
    h = host(tmp_path)
    (tmp_path / "projects" / "alpha").mkdir()
    assert await h.call("agents_list_projects", {}) == "alpha"
    monkeypatch.setattr("minty_core.plugins.builtin.agents.jobs.shutil.which",
                        lambda b: "/usr/bin/claude" if b == "claude" else None)
    assert await h.call("agents_list_agents", {}) == "claude_code"
    monkeypatch.setattr("minty_core.plugins.builtin.agents.jobs.shutil.which", lambda b: None)
    assert await h.call("agents_list_agents", {}) == "No coding agents are installed."
    assert await h.call("agents_task_status", {}) == "No jobs yet."


async def test_bad_project_name_is_an_error_string(tmp_path):  # RF7
    h = host(tmp_path)
    out = await h.call("agents_start_task", {"agent": "claude_code", "project": "../x", "prompt": "hi"})
    assert out.startswith("Error: ValueError")
    assert not (tmp_path / "x").exists()


async def test_reload_does_not_break_the_plugin(tmp_path):
    h = host(tmp_path)
    h.reload()
    assert await h.call("agents_task_status", {}) == "No jobs yet."
    assert await h.call("agents_list_projects", {}) == "No projects yet."


def test_permission_mode_is_allowlisted(caplog):
    from minty_core.plugins.builtin.agents.plugin import build_adapters
    for mode, expected in (("bypassPermissions", "acceptEdits"), ("plan", "plan"), ("", "acceptEdits")):
        inv = build_adapters({"claude_permission_mode": mode})["claude_code"].build(Path("."), "x", None)
        assert inv.argv[inv.argv.index("--permission-mode") + 1] == expected
    assert "bypassPermissions" in caplog.text


def test_all_three_adapters_are_registered(tmp_path):
    from minty_core.plugins.builtin.agents.plugin import build_adapters
    assert set(build_adapters({})) == {"claude_code", "hermes", "openclaw"}
    assert build_adapters({"openclaw_agent": "helper"})["openclaw"].build(tmp_path, "x", None).argv[3] == "helper"
    assert build_adapters({})["openclaw"].build(tmp_path, "x", None).argv[3] == "main"


def test_unsafe_permission_mode_still_falls_back_with_all_adapters(tmp_path):
    from minty_core.plugins.builtin.agents.plugin import build_adapters
    argv = build_adapters({"claude_permission_mode": "bypassPermissions"})["claude_code"].build(
        tmp_path, "x", None).argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_starting_tools_name_the_action_in_their_confirmation(tmp_path):
    h = host(tmp_path)
    task = h.get_tool("agents_start_task").describe(
        {"agent": "claude_code", "project": "alpha", "prompt": "fix the login bug " + "x" * 200})
    assert task.startswith("Should I have claude code work on alpha: fix the login bug ") and task.endswith("?")
    assert len(task) < 160
    project = h.get_tool("agents_start_project").describe({"agent": "hermes", "name": "beta", "prompt": "scaffold it"})
    assert project == "Should I have hermes work on new project beta: scaffold it?"
    assert h.get_tool("agents_task_status").describe is None


async def test_coding_jobs_appear_in_the_shared_job_list_and_cancel_through_it(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from minty_core.jobs import JobRunner
    from minty_core.plugins.builtin.agents.jobs import JobManager

    fake = NS(id=5, agent="claude_code", project="alpha", status="running", summary="", mode="work")
    stopped = []

    async def start(self, agent, project, prompt, *, create=False, mode="work"):
        return fake

    async def stop(self, job_id):
        stopped.append(job_id)
        fake.status = "stopped"
        return "Stopped."

    monkeypatch.setattr(JobManager, "start", start)
    monkeypatch.setattr(JobManager, "stop", stop)
    runner = JobRunner(None)
    root = tmp_path / "projects"
    root.mkdir()
    h = make_host(tmp_path, plugins={"agents": {"enabled": True, "root": str(root)}}, services={"jobs": runner})
    assert "Started job 5" in await h.call("agents_start_task", {"agent": "claude_code", "project": "alpha",
                                                                  "prompt": "fix the tests"})
    await asyncio.sleep(0.01)
    [shown] = runner.running()
    assert shown.kind == "coding" and shown.goal == "claude code on alpha: fix the tests"
    assert h._state["agents"]["mirrors"] == {5: shown.id}
    await runner.cancel(shown.id)
    assert stopped == [5] and shown.status == "cancelled"


def test_start_task_can_be_a_question_or_a_whole_job(tmp_path):
    tool = host(tmp_path).get_tool("agents_start_task")
    mode = tool.parameters["properties"]["mode"]
    assert mode["enum"] == ["work", "ask"] and "mode" not in tool.parameters["required"]
    assert "ask" in tool.describe({"agent": "claude_code", "project": "p", "prompt": "why?", "mode": "ask"}).lower()
    assert "work on" in tool.describe({"agent": "claude_code", "project": "p", "prompt": "fix it"})


def test_settings_reach_the_manager_and_the_adapter(tmp_path):
    from minty_core.plugins.builtin.agents.plugin import build_adapters
    argv = build_adapters({"claude_max_budget_usd": 3})["claude_code"].build(Path("."), "x", None).argv
    assert argv[argv.index("--max-budget-usd") + 1] == "3"


def test_a_check_command_with_shell_syntax_is_ignored(tmp_path, caplog):
    from minty_core.plugins.builtin.agents.plugin import configured_check
    assert configured_check({"check_command": "pytest -q"}) == ["pytest", "-q"]
    assert configured_check({}) is None
    assert configured_check({"check_command": "pytest; rm -rf ~"}) is None
    assert "check_command" in caplog.text


async def test_status_of_a_fresh_install(tmp_path):
    h = host(tmp_path, check_command="pytest -q", max_rounds=3)
    assert await h.call("agents_task_status", {}) == "No jobs yet."
