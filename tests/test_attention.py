"""Open loops: what needs the user, noticed without being asked, said rarely, and never twice for the same thing."""
from minty_core.attention import Attention, Loop

H = 3600.0


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make(tmp_path, sources, clock=None, **kw):
    clock = clock or Clock()
    nudges = []
    att = Attention(tmp_path / "attention.sqlite", sources, nudge=nudges.append, clock=clock, **kw)
    return att, nudges, clock


def loop(key="mission:3:blocked", text="Mission 3 needs you: which date?", urgency="now", since=None, kind="mission"):
    return Loop(key, kind, text, 1_000_000.0 if since is None else since, urgency)


def test_a_new_urgent_loop_is_said_once_straight_away(tmp_path):
    att, nudges, _ = make(tmp_path, {"jobs": lambda: [loop()]})
    att.tick()
    assert nudges == ["Mission 3 needs you: which date?"]


def test_the_same_loop_is_not_said_again_before_its_cooldown(tmp_path):
    att, nudges, clock = make(tmp_path, {"jobs": lambda: [loop()]})
    att.tick()
    clock.advance(1 * H)
    att.tick()
    clock.advance(4 * H)
    att.tick()
    assert len(nudges) == 1
    clock.advance(2 * H)  # six hours after the first
    att.tick()
    assert len(nudges) == 2


def test_a_loop_is_said_at_most_three_times_then_left_to_the_review(tmp_path):
    att, nudges, clock = make(tmp_path, {"jobs": lambda: [loop()]})
    for _ in range(10):
        att.tick()
        clock.advance(7 * H)
    assert len(nudges) == 3
    assert any("needs you" in line for line in att.review().splitlines())  # still on the list when asked


def test_a_less_urgent_loop_waits_before_it_is_first_said(tmp_path):
    att, nudges, clock = make(tmp_path, {"b": lambda: [loop("butler:todo:1", "Overdue: renew the library card", "today",
                                                            since=clock_now(), kind="todo")]})
    att.tick()
    assert nudges == []
    clock.advance(2.5 * H)
    att.tick()
    assert nudges == ["Overdue: renew the library card"]


def clock_now():
    return 1_000_000.0


def test_a_loop_that_resolved_disappears_and_a_new_one_with_the_same_key_is_new(tmp_path):
    live = [loop()]
    att, nudges, clock = make(tmp_path, {"jobs": lambda: list(live)})
    att.tick()
    live.clear()
    clock.advance(1 * H)
    att.tick()
    assert att.open_loops() == []
    live.append(loop())
    clock.advance(1 * H)
    att.tick()
    assert len(nudges) == 2  # not "already said": it is a different occasion


def test_dismissing_ends_it_and_snoozing_pauses_it(tmp_path):
    live = [loop(), loop("outbox:2:unknown", "An e-mail to Sam may not have been sent", kind="outbox")]
    att, nudges, clock = make(tmp_path, {"x": lambda: list(live)})
    att.tick()
    nudges.clear()
    assert att.dismiss("mission:3:blocked") and att.snooze("outbox:2:unknown", hours=24)
    for _ in range(4):
        clock.advance(7 * H)
        att.tick()
    assert not any("Mission 3" in n for n in nudges)  # dismissed: never again, while it stays open
    assert any("e-mail to Sam" in n for n in nudges)  # snoozed a day: back after the day
    assert not att.dismiss("nonexistent") and not att.snooze("nonexistent", hours=1)


def test_a_daily_budget_keeps_it_from_nagging(tmp_path):
    many = [loop(f"mission:{i}:blocked", f"Mission {i} needs you", kind="mission") for i in range(12)]
    att, nudges, _ = make(tmp_path, {"jobs": lambda: many}, max_per_day=5)
    att.tick()
    assert len(nudges) == 5
    att.tick()
    assert len(nudges) == 5


def test_a_broken_source_never_stops_the_others(tmp_path):
    def broken():
        raise RuntimeError("boom")

    att, nudges, _ = make(tmp_path, {"bad": broken, "good": lambda: [loop()]})
    att.tick()
    assert nudges == ["Mission 3 needs you: which date?"]
    assert "bad" in att.problems


def test_the_review_lists_everything_with_its_age_and_most_urgent_first(tmp_path):
    clock = Clock()
    src = {"x": lambda: [loop("a", "Overdue: file the form", "later", since=clock() - 3 * 86400, kind="todo"),
                         loop("b", "Mission 5 needs you: which one?", "now", since=clock() - 2 * H)]}
    att, _, _ = make(tmp_path, src, clock=clock)
    text = att.review()
    assert text.index("Mission 5") < text.index("file the form")
    assert "2 hours" in text and "3 days" in text
    assert make(tmp_path / "empty", {})[0].review() == "Nothing needs you right now."


def test_the_state_survives_a_restart(tmp_path):
    att, nudges, clock = make(tmp_path, {"jobs": lambda: [loop()]})
    att.tick()
    again, nudges2, _ = make(tmp_path, {"jobs": lambda: [loop()]}, clock=clock)
    clock.advance(1 * H)
    again.tick()
    assert nudges2 == []  # it remembers it already said this
