"""The post queue and the policy for what may be published without asking."""
import time

import pytest

from minty_core.studio.postpolicy import PostFacts, PostPolicy, decide
from minty_core.studio.posts import DuplicatePost, PostStore


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    s = PostStore(tmp_path / "posts.sqlite", clock=clock)
    s.clock = clock
    return s


def add(store, **kw):
    base = dict(kind="reel", files=["/x/a.mp4"], caption="Minecraft parkour #minecraft", content_hash="h1", problems=[], licensed=True)
    return store.add(**{**base, **kw})


def test_a_draft_is_stored_and_listed(store):
    p = add(store)
    assert p.status == "drafted" and p.files == ["/x/a.mp4"] and store.get(p.id).caption.startswith("Minecraft")
    assert [x.id for x in store.recent()] == [p.id]


def test_the_same_video_cannot_be_queued_or_posted_twice(store):
    first = add(store)
    with pytest.raises(DuplicatePost, match=f"post {first.id}"):
        add(store)
    store.discard(first.id)
    add(store)  # a discarded one is gone: the same video may be queued again


def test_only_one_caller_can_claim_a_post_and_a_finished_one_cannot_be_claimed(store):
    p = add(store)
    assert store.claim(p.id, "approved") is True and store.claim(p.id, "approved") is False
    store.finish(p.id, "posted", media_id="179", permalink="https://instagram.com/reel/abc/")
    assert store.get(p.id).status == "posted" and store.claim(p.id, "auto") is False


def test_a_failed_post_can_be_tried_again_but_an_unknown_one_cannot(store):
    p = add(store)
    store.claim(p.id, "approved")
    store.finish(p.id, "failed", error="upload failed")
    assert store.claim(p.id, "approved") is True
    store.finish(p.id, "unknown", error="the connection dropped after publish")
    assert store.claim(p.id, "approved") is False and store.discard(p.id) is True


def test_a_crash_mid_publish_is_unknown_after_the_restart(tmp_path):
    s = PostStore(tmp_path / "p.sqlite")
    p = add(s)
    s.claim(p.id, "approved")
    again = PostStore(tmp_path / "p.sqlite")
    assert again.get(p.id).status == "unknown" and "stopped" in again.get(p.id).error


def test_the_daily_count_and_the_gap_only_count_what_really_went_out(store):
    a, b = add(store), add(store, content_hash="h2")
    store.claim(a.id, "auto")
    store.finish(a.id, "posted", media_id="1")
    store.claim(b.id, "approved")
    store.finish(b.id, "failed", error="x")
    assert store.posted_today() == 1 and store.posted_today("auto") == 1 and store.posted_today("approved") == 0
    assert store.hours_since_last_post() == pytest.approx(0.0, abs=0.01)
    store.clock.t += 5 * 3600
    assert store.hours_since_last_post() == pytest.approx(5.0, abs=0.01)
    store.clock.t += 30 * 3600
    assert store.posted_today() == 0
    assert PostStore(store.path).hours_since_last_post() is not None


def test_the_record_of_approved_posts_for_earned_independence(store):
    for i in range(3):
        p = add(store, content_hash=f"h{i}")
        store.claim(p.id, "approved")
        store.finish(p.id, "posted", media_id=str(i))
    assert store.track_record() == {"approved_posts": 3, "bad": 0}
    p = add(store, content_hash="hx")
    store.claim(p.id, "approved")
    store.finish(p.id, "unknown", error="x")
    assert store.track_record() == {"approved_posts": 3, "bad": 1}


# ---- the policy ------------------------------------------------------------------------------------------------------------
def facts(**kw):
    base = dict(kind="reel", caption="Minecraft parkour in 30 seconds #minecraft #parkour", problems=[], licensed=True)
    return PostFacts(**{**base, **kw})


def check(f=None, p=None, today=0, since=None):
    return decide(p or PostPolicy(mode="auto"), f or facts(), today, since)


def test_by_default_every_post_is_asked():
    out = check(p=PostPolicy())
    assert out.mode == "ask" and any("ask first" in r for r in out.reasons)


def test_auto_needs_the_user_to_switch_it_on_and_everything_clean():
    assert check().mode == "auto" and check().reasons == []


@pytest.mark.parametrize("caption,why", [("see https://x.com/a", "link"), ("thanks @someone", "mention"),
                                         ("this is my password", "sensitive"), ("#" + " #".join(f"t{i}" for i in range(16)), "hashtags")])
def test_a_caption_that_could_be_spam_or_sensitive_is_asked(caption, why):
    out = check(facts(caption=caption))
    assert out.mode == "ask" and any(why in r for r in out.reasons)


def test_a_file_without_a_clean_licence_is_never_automatic():
    out = check(facts(licensed=False))
    assert out.mode == "ask" and any("licence" in r for r in out.reasons)


def test_the_daily_cap_and_the_gap_between_posts_turn_auto_into_asking():
    assert check(today=1).mode == "ask" and any("1 a day" in r for r in check(today=1).reasons)
    assert check(since=2.0).mode == "ask" and any("hours" in r for r in check(since=2.0).reasons)
    assert check(since=8.0).mode == "auto"


def test_a_video_that_failed_its_checks_is_refused_outright():
    out = check(facts(problems=["the picture looks black (brightest frame 16)"]))
    assert out.mode == "refuse" and "black" in out.reasons[0]


def test_hard_limits_are_refused():
    assert check(facts(caption="x" * 2300)).mode == "refuse"
    assert check(facts(caption="#a " * 31)).mode == "refuse"
    assert check(facts(caption="   ")).mode == "refuse"


def test_how_a_post_did_is_kept_with_it_and_tags_say_what_kind_of_post_it_was(store):
    a = add(store, tags="parkour,facts")
    store.claim(a.id, "approved")
    store.finish(a.id, "posted", media_id="1")
    assert store.get(a.id).tags == "parkour,facts" and store.get(a.id).insights == {}
    store.set_insights(a.id, {"reach": 120, "likes": 9})
    assert store.get(a.id).insights == {"reach": 120, "likes": 9}
    assert [p.id for p in store.posted_with_media()] == [a.id]
    store.claim(add(store, content_hash="other").id, "approved")  # still publishing: no media yet
    assert [p.id for p in store.posted_with_media()] == [a.id]
