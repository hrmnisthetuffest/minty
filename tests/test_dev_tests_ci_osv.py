"""648 test runner summaries, 649 CI watcher, 650 OSV radar."""
import json
import shutil

import pytest

from minty_core.dev.ci import CiWatcher, parse_runs, summarise_runs
from minty_core.dev.osv import Osv, parse_lockfiles, radar
from minty_core.dev.testrun import make_runner, parse_pytest, run_tests, summarise
from minty_core.system.runner import Result

FAILING = """
def test_ok_1(): assert True
def test_ok_2(): assert 1 + 1 == 2
def test_ok_3(): assert "a" in "abc"
def test_bad_math(): assert 2 + 2 == 5
def test_bad_text(): assert "hello".upper() == "hello"
"""

SAMPLE = """..F.F                                                                     [100%]
=================================== FAILURES ===================================
____________________________________ test_x ____________________________________
tests/test_a.py:4: AssertionError
=========================== short test summary info ============================
FAILED tests/test_a.py::test_bad_math - assert (2 + 2) == 5
FAILED tests/test_a.py::test_bad_text - AssertionError: assert 'HELLO' == 'hello'
2 failed, 3 passed in 0.12s
"""


def test_pytest_output_is_parsed_into_counts_and_failures():
    r = parse_pytest(SAMPLE)
    assert (r.failed, r.passed) == (2, 3) and r.seconds == 0.12
    assert r.failures[0] == ("tests/test_a.py::test_bad_math", "assert (2 + 2) == 5")


def test_the_summary_is_at_most_five_lines_however_many_tests_fail():
    many = "\n".join(f"FAILED tests/test_a.py::test_{i} - boom {i}" for i in range(30)) + "\n30 failed, 2 passed in 3.00s\n"
    text = summarise(parse_pytest(many), exit_code=1)
    assert len(text.splitlines()) <= 5
    assert text.splitlines()[0] == "30 failed, 2 passed in 3.0s." and "and 27 more" in text.splitlines()[-1]


def test_a_green_run_is_one_line_and_a_crash_before_any_test_shows_the_tail():
    assert summarise(parse_pytest("5 passed in 0.50s\n"), exit_code=0) == "All 5 tests passed in 0.5s."
    broken = parse_pytest("ImportError: cannot import name x\nERROR collecting tests/test_a.py\n!!! Interrupted: 1 error !!!\n")
    text = summarise(broken, exit_code=2, tail="ImportError: cannot import name x\nERROR collecting tests/test_a.py")
    assert "could not run" in text.lower() and "ImportError" in text and len(text.splitlines()) <= 5


@pytest.mark.skipif(shutil.which("pytest") is None, reason="pytest binary not on PATH")
async def test_a_seeded_failing_suite_gives_a_five_line_summary_from_a_real_run(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(FAILING)
    out = await run_tests(tmp_path, make_runner(), "pytest")
    assert len(out.splitlines()) <= 5
    assert out.splitlines()[0].startswith("2 failed, 3 passed") and "test_bad_math" in out and "test_bad_text" in out


async def test_only_known_test_commands_run_and_the_filter_cannot_carry_an_option(tmp_path):
    for bad in ("rm -rf /", "pytest; rm -rf /", "bash", "python3 -c 'print(1)'"):
        with pytest.raises(ValueError):
            await run_tests(tmp_path, make_runner(), bad)
    with pytest.raises(ValueError):
        await run_tests(tmp_path, make_runner(), "pytest", k="--collect-only; x")
    with pytest.raises(ValueError):
        await run_tests(tmp_path, make_runner(), "pytest", path="../../etc")


# ---- CI ------------------------------------------------------------------------------------------------------------
RUNS = json.dumps([
    {"databaseId": 3, "status": "completed", "conclusion": "failure", "workflowName": "tests", "headBranch": "main",
     "displayTitle": "Fix parser", "url": "https://github.com/h/p/actions/runs/3"},
    {"databaseId": 2, "status": "completed", "conclusion": "success", "workflowName": "tests", "headBranch": "main",
     "displayTitle": "Add cli", "url": "https://github.com/h/p/actions/runs/2"},
    {"databaseId": 4, "status": "in_progress", "conclusion": "", "workflowName": "lint", "headBranch": "dev",
     "displayTitle": "wip", "url": "u"}])


def test_runs_parse_and_summarise():
    runs = parse_runs(RUNS)
    assert [r.id for r in runs] == [3, 2, 4]
    text = summarise_runs(runs)
    assert "tests on main: failed" in text and "lint on dev: running" in text and len(text.splitlines()) <= 5
    assert parse_runs("not json") == [] and "No CI runs" in summarise_runs([])


class FakeGh:
    def __init__(self, outs):
        self.outs, self.calls, self.installed = list(outs), [], True

    def have(self, name):
        return self.installed

    async def run(self, argv, timeout=None, cwd=None):
        self.calls.append((argv, cwd))
        return Result(0, self.outs.pop(0) if len(self.outs) > 1 else self.outs[0])


async def test_the_watcher_speaks_on_a_change_not_on_every_poll_and_never_faster_than_five_minutes():
    t = [0.0]
    green = json.dumps([{"databaseId": 1, "status": "completed", "conclusion": "success", "workflowName": "tests",
                         "headBranch": "main", "displayTitle": "ok", "url": "u"}])
    red = json.dumps([{"databaseId": 2, "status": "completed", "conclusion": "failure", "workflowName": "tests",
                       "headBranch": "main", "displayTitle": "Fix parser", "url": "u"}])
    gh = FakeGh([green, green, red, red, green])
    w = CiWatcher(gh, clock=lambda: t[0], min_gap_s=300)
    assert await w.poll("/p/demo", "demo") == []  # first look: remembered silently
    t[0] += 60
    assert await w.poll("/p/demo", "demo") == [] and len(gh.calls) == 1  # too soon: no request at all
    t[0] += 300
    msg = await w.poll("/p/demo", "demo")
    assert len(gh.calls) == 2 and msg == []  # unchanged
    t[0] += 301
    msg = await w.poll("/p/demo", "demo")
    assert msg == ["CI failed on demo: tests on main (Fix parser)."]
    t[0] += 301
    assert await w.poll("/p/demo", "demo") == []  # still red: said once
    t[0] += 301
    assert await w.poll("/p/demo", "demo") == ["CI is green again on demo: tests on main."]


async def test_without_gh_it_says_how_to_get_it():
    gh = FakeGh([""])
    gh.installed = False
    w = CiWatcher(gh, clock=lambda: 0.0)
    out = await w.poll("/p", "demo")
    assert out and "GitHub CLI" in out[0] and gh.calls == []


# ---- OSV ----------------------------------------------------------------------------------------------------------
def test_lockfiles_give_pinned_packages_only(tmp_path):
    (tmp_path / "requirements.txt").write_text("# comment\nrequests==2.19.0\nflask>=2.0\n-e .\nrich==13.7.0  # pinned\n")
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {
        "": {"name": "x"}, "node_modules/lodash": {"version": "4.17.15"},
        "node_modules/a/node_modules/b": {"version": "1.0.0"}}}))
    (tmp_path / "Cargo.lock").write_text('[[package]]\nname = "serde"\nversion = "1.0.100"\n')
    got = parse_lockfiles(tmp_path)
    assert ("PyPI", "requests", "2.19.0") in got and ("PyPI", "rich", "13.7.0") in got
    assert all(p[1] != "flask" for p in got)
    assert ("npm", "lodash", "4.17.15") in got and ("npm", "b", "1.0.0") in got and ("crates.io", "serde", "1.0.100") in got


class FakeOsv:
    def __init__(self):
        self.posts, self.gets = [], []

    async def post(self, url, body):
        self.posts.append((url, body))
        results = [{"vulns": [{"id": "GHSA-xxxx"}]} if q["package"]["name"] == "requests" else {} for q in body["queries"]]
        return 200, {"results": results}

    async def get(self, url):
        self.gets.append(url)
        return 200, {"id": "GHSA-xxxx", "summary": "Requests leaks credentials on redirect",
                     "database_specific": {"severity": "MODERATE"},
                     "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.31.0"}]}]}]}


async def test_the_radar_sends_only_names_and_versions_caches_and_reports_the_fix(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "requirements.txt").write_text("requests==2.19.0\nrich==13.7.0\n")
    net = FakeOsv()
    osv = Osv(net.post, net.get, tmp_path / "cache.json", clock=lambda: 1000.0, sleep=lambda s: _nap())
    text = await radar(tmp_path / "proj", osv)
    assert "Checked 2 pinned packages" in text and "requests 2.19.0" in text and "GHSA-xxxx" in text
    assert "MODERATE" in text and "fixed in 2.31.0" in text
    (url, body), = net.posts
    assert url == "https://api.osv.dev/v1/querybatch"
    assert body == {"queries": [{"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.19.0"},
                                {"package": {"name": "rich", "ecosystem": "PyPI"}, "version": "13.7.0"}]}
    again = await radar(tmp_path / "proj", osv)  # a second run costs no requests
    assert len(net.posts) == 1 and len(net.gets) == 1 and "requests 2.19.0" in again


async def _nap():
    return None


async def test_no_pinned_packages_is_said_and_nothing_is_sent(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "requirements.txt").write_text("flask>=2\n")
    net = FakeOsv()
    text = await radar(tmp_path / "p", Osv(net.post, net.get, tmp_path / "c.json", clock=lambda: 0.0))
    assert "No pinned packages" in text and net.posts == []


async def test_the_radar_is_rate_limited_and_survives_a_failing_service(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "requirements.txt").write_text("\n".join(f"pkg{i}==1.0" for i in range(450)))

    class Down:
        posts = 0

        async def post(self, url, body):
            Down.posts += 1
            return 503, {}

        async def get(self, url):
            return 503, {}

    naps = []

    async def nap(s):
        naps.append(s)

    text = await radar(tmp_path / "p", Osv(Down().post, Down().get, tmp_path / "c.json", clock=lambda: 0.0, sleep=nap))
    assert "could not be checked" in text and Down.posts <= 3 and all(n >= 2 for n in naps)
