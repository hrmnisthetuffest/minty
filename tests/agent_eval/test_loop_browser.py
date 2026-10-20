"""The loop on the real browser plugin and the fixture shop (scripted model, no tokens)."""
from tests.agent_eval.conftest import needs_chrome
from tests.agent_eval.runner import run_task
from tests.agent_eval.scripted import ScriptedRouter, ref_of
from tests.agent_eval.tasks import BY_ID


@needs_chrome
async def test_the_captcha_wall_is_handed_to_the_user_not_looped_on(site):
    router = ScriptedRouter([
        lambda last: ("tool", "browser_open", {"url": site.url + "/captcha"}),
        lambda last: ("tool", "browser_click", {"ref": "e3"}),  # the checkbox that unchecks itself
    ])
    result = await run_task(BY_ID["captcha"], router, site=site)
    assert result["steps"] <= 3 and "CAPTCHA" in result["reply"] and "window is open" in result["reply"]
    assert not result["forbidden_hit"]  # handed to the user, never retried


@needs_chrome
async def test_a_false_done_is_caught_by_what_the_page_shows(site):
    verdicts = []

    def verifier(user):
        ok = "/cart" in user.split("Evidence from the world:")[1].split("Worker's claim")[0]
        verdicts.append(ok)
        return (ok, "the page is not the cart" if not ok else "on the cart")

    router = ScriptedRouter([
        lambda last: ("say", "Done, the Reading Lamp is in your cart."),  # claims without doing anything
        lambda last: ("tool", "browser_open", {"url": site.url + "/p/3"}),
        lambda last: ("tool", "browser_click", {"ref": ref_of(last, "Add to cart")}),
        lambda last: ("say", "Added the Reading Lamp; the cart page is open."),
    ], verifier=verifier)
    result = await run_task(BY_ID["add_to_cart"], router, site=site)
    assert verdicts == [False, True] and result["passed"] and not result["forbidden_hit"]
    assert "cart page" in result["reply"]


@needs_chrome
async def test_a_verified_multi_step_job_is_offered_for_learning(site):
    router = ScriptedRouter([
        lambda last: ("tool", "browser_open", {"url": site.url + "/p/3"}),
        lambda last: ("tool", "browser_click", {"ref": ref_of(last, "Add to cart")}),
        lambda last: ("tool", "browser_open", {"url": site.url + "/cart"}),
        lambda last: ("say", "The Reading Lamp is in the cart."),
    ])
    result = await run_task(BY_ID["add_to_cart"], router, site=site)
    assert result["passed"] and result["reply"].endswith("Want me to remember how to do this?")
