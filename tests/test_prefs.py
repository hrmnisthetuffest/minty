"""What Minty learns about how you work: told, repeated, inferred, temporary or only suggested; scoped, explained, correctable."""
import os
import stat

import pytest

from minty_core.prefs import PreferenceStore

DAY = 86400.0


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = PreferenceStore(tmp_path / "prefs.db", clock=clock)
    yield s
    s.close()


def test_what_the_user_says_applies_at_once_and_says_where_it_came_from(store):
    store.set_explicit("research->sheet", "research", note="you said: always put research in a sheet")
    assert store.value("research->sheet", ("research", "global")) == "1"
    p = store.list()[0]
    assert p.source == "explicit" and p.confidence == 1.0 and "you said" in store.explain(p) and "research" in store.explain(p)


def test_one_or_two_observations_are_only_a_guess_and_change_nothing(store):
    store.observe("research->sheet", "research", ref="a")
    store.observe("research->sheet", "research", ref="b")
    assert store.value("research->sheet", ("research",)) is None and store.suggestions() == []
    assert store.list(status="candidate")[0].evidence == 2


def test_three_is_a_suggestion_still_not_a_rule_until_the_user_accepts_it(store):
    for ref in "abc":
        store.observe("research->sheet", "research", ref=ref)
    [s] = store.suggestions()
    assert s.status == "suggested" and 0.5 < s.confidence < 1.0
    assert store.value("research->sheet", ("research",)) is None  # a repeated habit is offered, never silently applied
    assert store.accept(s.id) is True
    assert store.value("research->sheet", ("research",)) == "1" and store.list(status="active")[0].source == "repeated"


def test_the_same_event_counted_twice_is_one_observation(store):
    for _ in range(5):
        store.observe("research->sheet", "research", ref="same-goal")
    assert store.list(status="candidate")[0].evidence == 1


def test_a_preference_applies_in_its_own_scope_and_the_specific_scope_wins(store):
    store.set_explicit("research->sheet", "coding", value="0")
    store.set_explicit("research->sheet", "global", value="1")
    assert store.value("research->sheet", ("coding", "global")) == "0"  # coding projects: no sheet
    assert store.value("research->sheet", ("personal", "global")) == "1"
    assert store.value("research->sheet", ("personal",)) is None  # nothing says anything about personal on its own


def test_a_temporary_decision_lasts_for_that_task_only(store, clock):
    store.set_temporary("research->doc", "project:acme", "1", ttl_s=2 * 3600)
    assert store.value("research->doc", ("project:acme",)) == "1"
    clock.t += 3 * 3600
    assert store.value("research->doc", ("project:acme",)) is None


def test_a_correction_lowers_it_and_a_second_one_removes_it(store):
    store.set_explicit("mail.vip:alex@acme.test", "mail")
    store.observe("mail.mute:bob@news.test", "mail", ref="1")
    for ref in "abc":
        store.observe("mail.vip:carol@x.test", "mail", ref=ref)
    [s] = store.suggestions()
    store.accept(s.id)
    assert store.contradict("mail.vip:carol@x.test", "mail") == "lowered" and store.value("mail.vip:carol@x.test", ("mail",)) == "1"
    assert store.contradict("mail.vip:carol@x.test", "mail") == "removed" and store.value("mail.vip:carol@x.test", ("mail",)) is None
    assert store.contradict("mail.vip:nobody@x.test", "mail") == "unknown"


def test_what_was_rejected_or_removed_is_not_suggested_again_for_a_month(store, clock):
    for ref in "abc":
        store.observe("research->sheet", "research", ref=ref)
    store.reject(store.suggestions()[0].id)
    for ref in "defg":
        store.observe("research->sheet", "research", ref=ref)
    assert store.suggestions() == []  # it was said no to
    clock.t += 31 * DAY
    for ref in "hij":
        store.observe("research->sheet", "research", ref=ref)
    assert len(store.suggestions()) == 1  # a month later the habit may be offered again


def test_a_guess_nobody_repeats_fades_but_what_you_told_it_stays(store, clock):
    store.observe("research->doc", "research", ref="x")
    store.set_explicit("research->sheet", "research")
    clock.t += 200 * DAY
    store.decay()
    assert store.list(status="candidate") == [] and store.value("research->sheet", ("research",)) == "1"


def test_only_known_kinds_of_preference_exist_so_it_can_never_touch_permissions(store):
    for bad in ("safety.mode", "permissions->allow-all", "mail.vip:not an address", "research->", "", "shell->always"):
        with pytest.raises(ValueError):
            store.set_explicit(bad, "global")
    with pytest.raises(ValueError):
        store.observe("research->sheet", "x" * 80)  # a scope is a short plain word


def test_forgetting_one_removes_it_for_good(store):
    store.set_explicit("research->sheet", "research")
    pid = store.list()[0].id
    assert store.forget(pid) is True and store.value("research->sheet", ("research",)) is None and store.forget(pid) is False


def test_it_survives_a_restart_and_the_file_is_private(tmp_path, clock):
    path = tmp_path / "prefs.db"
    a = PreferenceStore(path, clock=clock)
    a.set_explicit("research->sheet", "research")
    a.close()
    b = PreferenceStore(path, clock=clock)
    assert b.value("research->sheet", ("research",)) == "1"
    b.close()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
