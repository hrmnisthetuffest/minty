"""The harness measures correctly (scripted model, real browser, real fixture)."""
import pytest

from tests.agent_eval.conftest import needs_chrome
from tests.agent_eval.metrics import pass_hat_k, summarise
from tests.agent_eval.runner import run_task
from tests.agent_eval.scripted import ScriptedRouter, ref_of
from tests.agent_eval.tasks import BY_ID


def test_pass_hat_k_matches_hand_computed_values():
    assert pass_hat_k(3, 3, 3) == 1.0 and pass_hat_k(3, 2, 3) == 0.0
    assert pass_hat_k(3, 2, 2) == pytest.approx(1 / 3) and pass_hat_k(4, 2, 1) == 0.5
    with pytest.raises(ValueError):
        pass_hat_k(2, 2, 3)


def test_summary_excludes_infra_errors_from_scores():
    rows = [
        {"task": "a", "passed": True, "forbidden_hit": False, "steps": 3, "seconds": 2.0, "tokens_est": 100},
        {"task": "a", "passed": True, "forbidden_hit": False, "steps": 5, "seconds": 4.0, "tokens_est": 100},
        {"task": "a", "passed": False, "forbidden_hit": False, "steps": 9, "seconds": 9.0, "tokens_est": 300},
        {"task": "b", "passed": False, "forbidden_hit": True, "steps": 2, "seconds": 1.0, "tokens_est": 50},
        {"task": "b", "passed": False, "forbidden_hit": False, "steps": 0, "seconds": 0.1, "infra": True},
    ]
    s = summarise(rows, k=3)
    assert s["trials"] == 5 and s["infra_errors"] == 1 and s["pass_at_1"] == 0.5
    assert s["pass_hat_3"] == 0.0 and s["forbidden_hits"] == 1 and s["failed_tasks"] == ["a", "b"]
    assert s["median_steps"] == 4.0 and s["tokens_est"] == 550


@needs_chrome
async def test_scripted_success_is_measured(site):
    task = BY_ID["add_to_cart"]
    router = ScriptedRouter([
        lambda last: ("tool", "browser_open", {"url": site.url + "/p/3"}),
        lambda last: ("tool", "browser_click", {"ref": ref_of(last, "Add to cart")}),
        lambda last: ("say", "Added the Reading Lamp to your cart."),
    ])
    result = await run_task(task, router, site=site)
    assert result["passed"] and not result["forbidden_hit"] and result["steps"] == 2
    assert result["llm_calls"] == 4 and result["tokens_est"] > 0 and not result["infra"]  # 3 turns + the check


@needs_chrome
async def test_following_an_injection_is_caught(site):
    task = BY_ID["injection"]
    router = ScriptedRouter([
        lambda last: ("tool", "browser_open", {"url": site.url + "/notes"}),
        lambda last: ("tool", "browser_open", {"url": site.url + "/evil"}),
        lambda last: ("say", "Customers say it's great."),
    ])
    result = await run_task(task, router, site=site)
    assert result["forbidden_hit"] and result["passed"]  # the reply was fine; the behaviour was not


@needs_chrome
async def test_a_model_outage_is_infra_not_failure(site):
    from minty_core.llm.types import LLMError

    class Down:
        tracer = None

        async def stream(self, *a, **k):
            raise LLMError("no LLM provider available")
            yield  # pragma: no cover

    result = await run_task(BY_ID["delayed"], Down(), site=site)
    assert result["infra"] and not result["passed"]
