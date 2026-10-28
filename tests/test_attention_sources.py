import asyncio
from types import SimpleNamespace

from tests.plugin_helpers import make_host
from minty_core.attention_sources import butler_loops, mission_loops
from minty_core.jobs import JobOutcome, JobRunner, make_job_store

NOW = 2_000_000.0


def runner(tmp_path):
    return JobRunner(make_job_store(tmp_path, "sqlite"), clock=lambda: NOW)


async def finished(r, kind, goal, outcome, **kw):
    async def work(ctx):
        return outcome
    job = r.submit(kind, goal, work, announce=False, **kw)
    await r.wait(job.id)
    return job


async def test_a_blocked_mission_is_a_loop_that_needs_the_user_now(tmp_path):
    r = runner(tmp_path)
    job = await finished(r, "mission", "book the dentist", JobOutcome("blocked", "Which day?"))
    loops = mission_loops(r, clock=lambda: NOW)
    assert [(l.key, l.urgency) for l in loops] == [(f"mission:{job.id}:blocked", "now")]
    assert "Mission" in loops[0].text and "Which day?" in loops[0].text and "answer" in loops[0].text


async def test_failed_and_interrupted_missions_can_be_picked_up_but_done_ones_are_left_alone(tmp_path):
    r = runner(tmp_path)
    await finished(r, "mission", "a", JobOutcome("done", "fine"))
    failed = await finished(r, "mission", "b", JobOutcome("failed", "I got stuck on step 2."), resumable=True)
    await finished(r, "browser", "c", JobOutcome("blocked", "captcha"))  # not a mission: its own announcement is enough
    loops = mission_loops(r, clock=lambda: NOW)
    assert [l.key for l in loops] == [f"mission:{failed.id}:failed"]
    assert loops[0].urgency == "later" and "resume" in loops[0].text.lower()


async def test_an_old_failure_is_forgotten(tmp_path):
    r = runner(tmp_path)
    job = await finished(r, "mission", "b", JobOutcome("failed", "stuck"), resumable=True)
    job.finished = NOW - 5 * 86400
    assert mission_loops(r, clock=lambda: NOW) == []


def butler_with(items, domains=("todo", "waiting", "place")):
    class B:
        def __init__(self):
            self.domains = {d: object() for d in domains}

        def list(self, domain, status="open"):
            return [i for i in items if i.domain == domain]

    return SimpleNamespace(butler=lambda: B())


def item(i, domain, name, due):
    return SimpleNamespace(id=i, domain=domain, name=name, due=due)


def test_overdue_items_and_follow_ups_due_are_loops_but_future_and_undated_ones_are_not():
    provider = butler_with([item(1, "todo", "renew the library card", NOW - 5 * 86400),
                            item(2, "waiting", "Sam: the form", NOW - 3600),
                            item(3, "todo", "next month", NOW + 86400), item(4, "todo", "someday", None),
                            item(5, "place", "keys", NOW - 86400)])  # a domain that is not about deadlines
    loops = {l.key: l for l in butler_loops(provider, clock=lambda: NOW)}
    assert set(loops) == {"butler:todo:1", "butler:waiting:2"}
    assert loops["butler:waiting:2"].text == "Follow up: Sam: the form" and loops["butler:waiting:2"].urgency == "today"
    assert loops["butler:todo:1"].text == "Overdue: renew the library card" and loops["butler:todo:1"].urgency == "later"


def test_a_butler_that_is_switched_off_has_no_loops():
    def off():
        raise ValueError("The butler is off")

    assert butler_loops(SimpleNamespace(butler=off), clock=lambda: NOW) == []
    assert butler_loops(None, clock=lambda: NOW) == []


async def test_the_attention_plugin_reviews_dismisses_and_nudges_through_the_scheduler(tmp_path):
    r = JobRunner(make_job_store(tmp_path, "sqlite"))
    await finished(r, "mission", "book the dentist", JobOutcome("blocked", "Which day?"))
    queued = []
    scheduler = SimpleNamespace(add=lambda text, due, **kw: queued.append((text, kw)))
    host = make_host(tmp_path, plugins={"attention": {"enabled": True}},
                     services={"jobs": r, "scheduler": scheduler})
    review = await host.call("attention_review", {})
    assert "Which day?" in review and "mission:1:blocked" in review
    att = host._services["attention"]
    att.tick()
    assert queued and "Which day?" in queued[0][0] and queued[0][1]["user_set"] is False and queued[0][1]["kind"] == "nudge"
    assert "Dismissed" in await host.call("attention_dismiss", {"key": "mission:1:blocked"})
    assert "Nothing needs you" in await host.call("attention_review", {})
    assert "No open item" in await host.call("attention_dismiss", {"key": "nope"})
    assert "No open item" in await host.call("attention_snooze", {"key": "nope", "hours": 3})


async def test_a_recipe_that_paused_itself_reaches_the_attention_list(tmp_path):
    from minty_core.attention import Loop
    loop = Loop("recipe:daily-reel:paused", "recipe", "The recipe daily-reel paused itself: it failed 3 times", 1.0, "today")
    host = make_host(tmp_path, plugins={"attention": {"enabled": True}, "recipes": {"enabled": False}},
                     services={"recipe_loops": lambda: [loop]})
    assert "daily-reel paused itself" in await host.call("attention_review", {})


async def test_studio_trouble_reaches_the_attention_list_and_earned_independence_reaches_the_review(tmp_path):
    from minty_core.attention import Loop
    from minty_core.worklessons import Lesson
    loop = Loop("studio:3:unknown", "studio", "Post 3 may or may not be on Instagram.", 1.0, "today")
    host = make_host(tmp_path, plugins={"attention": {"enabled": True}, "studio": {"enabled": False}, "missions": {"enabled": True}},
                     services={"studio_attention": lambda: [loop],
                               "studio_lessons": lambda: [Lesson("studio:autopost", "You said yes to 5 posts", "set post_mode to auto yourself", "confirmed", 5)]})
    assert "Post 3 may or may not" in await host.call("attention_review", {})
    assert "post_mode" in await host.call("missions_review", {})
