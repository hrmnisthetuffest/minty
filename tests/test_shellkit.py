"""The terminal's engine: what may run without asking, what is refused, and that a command runs in a contained process."""
import asyncio
import os
import time

import pytest

from minty_core.shellkit import blocked_reason, classify_command, run, safe_argv, sandbox_env


@pytest.mark.parametrize("command", ["pwd", "ls", "ls -la /tmp", "whoami", "date", "date +%Y-%m-%d", "date -u", "uname -a", "uptime",
                                     "df -h", "free -m", "hostname", "which python3", "git status", "git log --oneline -5",
                                     "git diff", "git branch", "  ls   -l  ", "ls 'a file with spaces'"])
def test_a_few_read_only_commands_run_without_asking(command):
    assert safe_argv(command) is not None and classify_command(command) == 0


@pytest.mark.parametrize("command", [
    "", "   ", "rm file", "cat notes.txt", "head -n 5 x", "env", "printenv", "find . -name x", "LS", "/bin/ls", "./ls",
    "ls; rm -rf ~", "ls && whoami", "ls || whoami", "ls | cat", "ls > out.txt", "ls < in.txt", "echo $(whoami)", "ls `pwd`", "ls $HOME",
    "ls\nwhoami", "ls & whoami", "ls 'unclosed",
    "git push", "git config user.name x", "git -c core.pager=x log", "git log --output=out.txt", "git diff --output out.txt", "git",
    "date -s tomorrow", "date --set=tomorrow", "hostname newname", "pwd extra", "whoami root", "uptime -x; ls",
])
def test_anything_else_is_a_command_that_must_be_asked_first(command):
    assert safe_argv(command) is None and classify_command(command) == 3


def test_git_reads_run_with_the_settings_that_could_run_code_switched_off():
    assert safe_argv("git status") == ["git", "--no-pager", "-c", "core.fsmonitor=false", "status"]
    log = safe_argv("git log --oneline -5")
    assert log[:4] == ["git", "--no-pager", "-c", "core.fsmonitor=false"] and "--no-ext-diff" in log and "--no-textconv" in log
    assert log[-2:] == ["--oneline", "-5"]
    assert safe_argv("git branch") == ["git", "--no-pager", "-c", "core.fsmonitor=false", "branch"]


@pytest.mark.parametrize("command", ["sudo ls", "echo hi; sudo rm x", "su -", "doas reboot", "pkexec bash", "ls && sudo x", "run0 id"])
def test_running_as_another_user_is_refused_before_anyone_is_asked(command):
    assert "another user" in blocked_reason(command)


def test_the_refusals_are_plain_and_do_not_catch_innocent_words():
    assert blocked_reason("echo pseudocode and result") is None
    assert blocked_reason("ls -la") is None
    assert "command" in blocked_reason("")
    assert "too long" in blocked_reason("echo " + "x" * 2100)
    assert blocked_reason("echo a\x00b")


def test_the_environment_has_no_keys_and_a_plain_terminal(monkeypatch):
    for name, value in {"GROQ_API_KEY": "gsk_secret", "AWS_SECRET_ACCESS_KEY": "aws", "MINTY_TOKEN": "t", "GITHUB_TOKEN": "g"}.items():
        monkeypatch.setenv(name, value)
    env = sandbox_env()
    assert not {"GROQ_API_KEY", "AWS_SECRET_ACCESS_KEY", "MINTY_TOKEN", "GITHUB_TOKEN"} & set(env)
    assert env["TERM"] == "dumb" and env["PAGER"] == "cat" and env["GIT_TERMINAL_PROMPT"] == "0" and "PATH" in env


async def test_a_command_runs_and_its_output_comes_back(tmp_path):
    r = await run("echo hello", cwd=tmp_path)
    assert r.code == 0 and r.text == "hello\n" and not r.timed_out
    r = await run("echo oops 1>&2; exit 3", cwd=tmp_path)
    assert r.code == 3 and "oops" in r.text  # errors are in the same stream, and a failing exit is a result, not a crash


async def test_the_command_runs_in_the_folder_it_was_given(tmp_path):
    assert (await run("pwd", cwd=tmp_path)).text.strip() == str(tmp_path.resolve())


async def test_the_command_cannot_read_a_key_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_do_not_leak")
    assert "gsk_do_not_leak" not in (await run("env", cwd=tmp_path)).text


async def test_a_command_waiting_for_input_gets_none_instead_of_hanging(tmp_path):
    started = time.perf_counter()
    r = await run("cat", cwd=tmp_path, timeout_s=5)
    assert r.code == 0 and r.text == "" and time.perf_counter() - started < 2


async def test_a_command_that_runs_too_long_is_stopped_with_everything_it_started(tmp_path):
    r = await run("sleep 60 & echo $!; wait", cwd=tmp_path, timeout_s=0.5)
    assert r.timed_out
    child = int(r.text.split()[0])
    for _ in range(30):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("the command's own child process is still running")


async def test_cancelling_the_turn_kills_the_command(tmp_path):
    pidfile = tmp_path / "pid"
    task = asyncio.create_task(run(f"echo $$ > {pidfile}; sleep 60", cwd=tmp_path, timeout_s=30))
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    pytest.fail("the command kept running after the turn was cancelled")


async def test_long_output_keeps_its_start_and_its_end(tmp_path):
    r = await run("seq 1 100000", cwd=tmp_path, max_chars=400)
    assert r.truncated and len(r.text) < 700
    assert r.text.startswith("1\n2\n") and r.text.rstrip().endswith("100000") and "cut" in r.text


async def test_endless_output_is_stopped_instead_of_filling_memory(tmp_path):
    started = time.perf_counter()
    r = await run("yes", cwd=tmp_path, timeout_s=20, hard_bytes=500_000)
    assert r.too_much and not r.timed_out and time.perf_counter() - started < 8


async def test_a_program_that_is_not_installed_is_a_result(tmp_path):
    r = await run(["definitely-not-a-program-xyz"], cwd=tmp_path)
    assert r.missing and r.code == 127
