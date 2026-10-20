"""Coding jobs get a fixed brief around the user's request (Wave 16 C1): scope, tests, no history rewrites, a short report."""
import asyncio

from minty_core.plugins.builtin.agents.adapters import Invocation, Parsed
from minty_core.plugins.builtin.agents.jobs import JOB_BRIEF, JobManager, with_brief


def test_the_brief_wraps_the_request_and_stays_short():
    out = with_brief("add a test for parse_args")
    assert out.endswith("add a test for parse_args") and JOB_BRIEF in out
    assert len(JOB_BRIEF) < 900
    low = JOB_BRIEF.lower()
    for needle in ("read", "test", "never", "push", "report", "changed"):
        assert needle in low, needle


def test_a_request_starting_with_a_dash_is_still_a_task_not_a_flag():
    assert not with_brief("--dangerous").startswith("-")
