"""What every request carries (system prompt + tool schemas, the follow-up after page readings) may not grow past scripts/perf_budget.toml.
These are counts of characters, not timings: they do not flicker. The timing budgets are checked by scripts/perf.py."""
import importlib.util
from minty_core._toml import tomllib
from pathlib import Path

import pytest

pytest.importorskip("resource")  # scripts/perf.py measures memory with it; Windows has no such module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("perf_script", ROOT / "scripts" / "perf.py")
perf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)
BUDGET = tomllib.loads((ROOT / "scripts" / "perf_budget.toml").read_text())


def within(name, value):
    assert value <= BUDGET[name], f"{name} is {value:.0f}, over its budget of {BUDGET[name]} (scripts/perf_budget.toml)"


def test_a_plain_question_and_a_shopping_request_stay_within_their_budgets():
    within("prompt_tokens_plain", perf.prompt_tokens("what time is it"))
    within("prompt_tokens_shopping", perf.prompt_tokens("find me the cheapest desk lamp on amazon"))


def test_the_tool_schemas_sent_with_every_request_stay_within_their_budget():
    sizes = perf.tool_schema_chars()
    within("tool_chars_core", sizes["tool_chars_core"])
    within("tool_chars_all", sizes["tool_chars_all"])


def test_a_follow_up_after_page_readings_stays_within_its_budget():
    got = perf.browser_followup()
    within("browser_followup_chars", got["browser_followup_chars"])
    assert got["browser_followup_tools"] <= BUDGET["browser_followup_tools"]
