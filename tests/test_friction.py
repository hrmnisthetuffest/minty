"""The manual work that is left: how many questions you answered, how long work waited for you, which ones you always say yes to."""
import pytest

from minty_core.approvals import ApprovalStore
from minty_core.friction import analyze, render, weekly_event
from minty_core.notifier import Event

DAY = 86400.0
NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW - 3 * DAY):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return ApprovalStore(tmp_path / "approvals.sqlite", clock=clock)


def ask(store, clock, tool, answer, risk=1, wait=20, **args):
    a = store.request(tool, args or {"n": clock.t}, f"Should I run {tool}?", risk)
    clock.t += wait
    if answer == "yes":
        store.decide(a.id, True, via="voice")
        store.consume(a.id, tool, args or {"n": a.created})
    elif answer == "no":
        store.decide(a.id, False, via="telegram")
    elif answer == "never":
        clock.t += 400  # nobody answered: it expires
        store._expire(clock.t)
    clock.t += 5
    return a


def test_it_counts_the_questions_and_the_time_work_waited_for_you(store, clock):
    for _ in range(4):
        ask(store, clock, "files_delete_old", "yes", wait=30)
    ask(store, clock, "agents_start_task", "no", wait=10)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7)
    assert f.asked == 5 and f.answered == 5 and abs(f.per_day - 5 / 7) < 0.01
    assert 120 + 10 - 1 <= f.wait_s <= 130 + 1  # 4 x 30 s + 10 s of waiting for you
    by = {t.tool: t for t in f.tools}
    assert by["files_delete_old"].approved == 4 and by["files_delete_old"].denied == 0 and by["agents_start_task"].denied == 1


def test_a_question_you_always_say_yes_to_gets_a_proposal_that_is_never_applied(store, clock):
    for _ in range(6):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: False)
    [p] = [p for p in f.proposals if "scenes_launch" in p]
    assert "6 times" in p and "never said no" in p and "minty butler standing" in p
    assert "applied" not in render(f).lower() or "nothing is changed" in render(f).lower()  # it only proposes


def test_when_the_tool_already_has_a_mandate_the_advice_is_to_ask_for_it_in_your_words(store, clock):
    for _ in range(6):
        ask(store, clock, "research_start", "yes", risk=3)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: tool == "research_start")
    p = next(p for p in f.proposals if "research_start" in p)
    assert "your own words" in p and "minty butler standing" not in p


def test_class_3_is_never_proposed_away(store, clock):
    for _ in range(8):
        ask(store, clock, "outbox_send", "yes", risk=3)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: False)
    assert not any("outbox_send" in p and "standing" in p for p in f.proposals)
    assert any("outbox_send" in p and "always asked" in p for p in f.proposals)


def test_questions_nobody_answered_and_ones_you_mostly_refuse_are_called_out(store, clock):
    for _ in range(3):
        ask(store, clock, "browser_macro_run", "never", risk=2)
    for answer in ("no", "no", "no", "yes"):
        ask(store, clock, "agents_start_project", answer, risk=2)
    text = " ".join(analyze(store.history(NOW - 7 * DAY), now=NOW, days=7).proposals)
    assert "browser_macro_run" in text and "went unanswered" in text
    assert "agents_start_project" in text and "usually say no" in text


def test_a_few_questions_are_not_a_problem(store, clock):
    ask(store, clock, "files_delete_old", "yes")
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7)
    assert f.proposals == [] and "nothing to fix" in render(f).lower()


def test_only_the_window_counts(store, clock):
    clock.t = NOW - 20 * DAY
    for _ in range(9):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    assert analyze(store.history(NOW - 7 * DAY), now=NOW, days=7).asked == 0


def test_the_weekly_note_is_sent_only_when_there_is_something_to_say_and_only_once_a_week(store, clock):
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7)
    assert weekly_event(f, week="2026-W44") is None  # a quiet week: nothing to say
    for _ in range(12):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: False)
    e = weekly_event(f, week="2026-W44")
    assert isinstance(e, Event) and e.source == "friction" and e.key == "week:2026-W44" and e.severity == "notable"
    assert "12" in e.title and "scenes_launch" in e.body


def test_a_week_of_only_always_asked_questions_gets_no_note(store, clock):
    for _ in range(12):
        ask(store, clock, "mcp_call", "yes", risk=3)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: False)
    assert any("always asked" in p for p in f.proposals) and "by design" in render(f)  # the report still says so when you ask
    assert weekly_event(f, week="2026-W44") is None  # but nothing could change, so nothing is said


def test_the_weekly_note_leaves_out_what_cannot_change(store, clock):
    for _ in range(12):
        ask(store, clock, "mcp_call", "yes", risk=3)
    for _ in range(6):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, has_mandate=lambda tool: False)
    e = weekly_event(f, week="2026-W44")
    assert e is not None and "scenes_launch" in e.body and "by design" not in e.body and "mcp_call" not in e.body


def test_the_history_is_the_decided_and_unanswered_questions_since_a_time(store, clock):
    a = ask(store, clock, "files_delete_old", "yes")
    open_one = store.request("agents_start_task", {"x": 1}, "Should I?", 2)
    rows = store.history(NOW - 7 * DAY)
    assert [r.tool for r in rows] == ["files_delete_old"] and rows[0].id == a.id  # a question still waiting is not counted yet
    assert open_one.status == "pending"


def test_the_weekly_note_goes_through_the_notifier_so_it_is_told_once_a_week(store, clock, tmp_path):
    from minty_core.friction import friction_note
    from tests.test_gmailwatch import Recorder

    for _ in range(12):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    rec = Recorder(tmp_path)
    for _ in range(3):  # the loop runs every few minutes; the week's note must not repeat
        friction_note(store, rec.notifier, lambda tool: None, now=NOW)
    told = [t for n, s, k, t in rec.told if n == "panel" and k == f"week:{__import__('datetime').datetime.fromtimestamp(NOW).strftime('%G-W%V')}"]
    assert len(told) == 1 and "12" in told[0] and "scenes_launch" in told[0]


async def test_the_notices_tool_shows_the_report(tmp_path, clock):
    from tests.plugin_helpers import make_host

    services = {}
    host = make_host(tmp_path, services=services)
    approvals = ApprovalStore(tmp_path / "ap.sqlite")
    for _ in range(6):
        a = approvals.request("scenes_launch", {"n": _}, "Should I?", 2)
        approvals.decide(a.id, True)
    services["approvals"] = approvals
    out = await host.call("notices_friction", {})
    assert "6 questions" in out and "scenes_launch" in out and "only proposes" in out


def test_the_cost_of_an_outcome_is_the_questions_it_took(store, clock):
    for _ in range(8):
        ask(store, clock, "scenes_launch", "yes", risk=2)
    f = analyze(store.history(NOW - 7 * DAY), now=NOW, days=7, outcomes=4)
    assert f.outcomes == 4 and "4 jobs finished" in render(f) and "2.0 questions" in render(f)
