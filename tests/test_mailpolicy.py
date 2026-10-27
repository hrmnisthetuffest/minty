"""The e-mail autonomy policy: which sends may go without asking, which are asked, which are refused. Plain rules, no model."""
import pytest

from minty_core.mailpolicy import Draft, Policy, decide, parse_list


def policy(**kw):
    base = dict(allow=("mum@home.in", "@school.edu"), never=("boss@rival.com",), max_per_day=3)
    return Policy(**{**base, **kw})


def draft(**kw):
    base = dict(to=("mum@home.in",), subject="Re: dinner", body="Sounds good, see you at 7.", reply=True,
                authenticated=True, original_subject="dinner?")
    return Draft(**{**base, **kw})


def check(d=None, p=None, sent_today=0, secrets=False):
    return decide(p or policy(), d or draft(), sent_today, has_secret=lambda text: secrets)


def test_a_short_reply_to_an_allowed_authenticated_sender_goes_without_asking():
    out = check()
    assert out.mode == "auto" and out.reasons == []


@pytest.mark.parametrize("to", ["teacher@school.edu", "MUM@home.in", "Mum@Home.in"])
def test_an_address_or_a_whole_domain_can_be_allowed_and_case_does_not_matter(to):
    assert check(draft(to=(to,))).mode == "auto"


def test_a_stranger_is_asked_and_the_reason_says_why():
    out = check(draft(to=("stranger@x.com",)))
    assert out.mode == "ask" and any("not on your auto-reply list" in r for r in out.reasons)


def test_a_new_conversation_is_always_asked_even_to_an_allowed_person():
    out = check(draft(reply=False, original_subject=""))
    assert out.mode == "ask" and any("not a reply" in r for r in out.reasons)


def test_an_unauthenticated_or_unknown_sender_could_be_spoofed_and_is_asked():
    assert any("could not confirm" in r for r in check(draft(authenticated=False)).reasons)
    assert check(draft(authenticated=None)).mode == "ask"
    assert check(draft(authenticated=None), policy(require_authenticated=False)).mode == "auto"


@pytest.mark.parametrize("body", ["My password is hunter2", "please pay the invoice", "our bank details", "call the lawyer"])
def test_sensitive_words_are_asked(body):
    out = check(draft(body=body))
    assert out.mode == "ask" and any("sensitive" in r for r in out.reasons)


def test_a_sensitive_word_in_the_message_being_answered_counts_too():
    assert check(draft(original_subject="Your bank statement")).mode == "ask"


def test_links_and_other_addresses_in_an_automatic_reply_are_asked():
    assert check(draft(body="see https://x.com/a")).mode == "ask"
    assert check(draft(body="ask bob@elsewhere.com")).mode == "ask"
    assert check(draft(body="Mum@home.in is right")).mode == "auto"  # the recipient's own address is fine


def test_a_long_message_or_one_with_a_secret_is_asked():
    assert check(draft(body="x " * 500)).mode == "ask"
    out = check(secrets=True)
    assert out.mode == "ask" and any("secret" in r for r in out.reasons)


def test_the_daily_cap_turns_the_rest_into_questions():
    assert check(sent_today=2).mode == "auto"
    out = check(sent_today=3)
    assert out.mode == "ask" and any("3 a day" in r for r in out.reasons)


def test_several_recipients_are_asked_and_a_crowd_is_refused():
    assert check(draft(to=("mum@home.in", "dad@home.in"))).mode == "ask"
    assert check(draft(to=tuple(f"p{i}@x.com" for i in range(9)))).mode == "refuse"


def test_the_never_list_wins_over_everything():
    out = check(draft(to=("boss@rival.com",)), policy(allow=("boss@rival.com",)))
    assert out.mode == "refuse" and any("never" in r for r in out.reasons)
    assert check(draft(to=("x@rival.com",)), policy(never=("@rival.com",))).mode == "refuse"


def test_an_empty_policy_never_sends_by_itself():
    out = check(p=Policy())
    assert out.mode == "ask"


def test_bad_addresses_and_header_tricks_are_refused():
    for bad in ("not-an-address", "a@b.c\nBcc: x@y.z", "a@b.com, c@d.com"):
        assert check(draft(to=(bad,))).mode == "refuse"
    assert check(draft(subject="hi\nBcc: spy@x.com")).mode == "refuse"
    assert check(draft(body="")).mode == "refuse"


def test_parse_list_is_forgiving():
    assert parse_list(" Mum@home.in, @School.edu ;  ,x@y.com\n") == ("mum@home.in", "@school.edu", "x@y.com")
    assert parse_list(None) == () and parse_list(5) == ()
