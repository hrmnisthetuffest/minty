"""Does this message matter? Several independent signals, never the sender alone and never one score from a model."""
from minty_core.mailscore import Context, Mail, score

ME = "me@example.com"


def mail(subject="", snippet="", sender="Alex <alex@acme.test>", labels=("INBOX", "UNREAD"), to=(ME,), headers=None, **kw):
    return Mail(id=kw.pop("id", "m1"), thread_id=kw.pop("thread_id", "t1"), sender=sender, to=tuple(to), subject=subject, snippet=snippet,
                labels=tuple(labels), headers=headers or {}, **kw)


def ctx(**kw):
    base = dict(me=(ME,), vips=(), topics=(), known=lambda sender: None, in_thread=lambda thread: None)
    base.update(kw)
    return Context(**base)


def test_a_bill_with_a_date_from_a_stranger_matters_and_says_why():
    v = score(mail("Invoice 4471 due Friday", "Please pay the attached invoice by Friday. Can you confirm?"), ctx())
    assert v.severity in ("important", "urgent") and v.action == "pay"
    assert any("deadline" in r for r in v.reasons) and any("money" in r.lower() or "invoice" in r.lower() for r in v.reasons)


def test_a_promotion_a_newsletter_and_a_code_are_not_worth_a_word():
    assert score(mail("50% off everything", "Sale ends tonight!", labels=("INBOX", "CATEGORY_PROMOTIONS")), ctx()).severity == "info"
    assert score(mail("This week in tech", "Our newsletter", headers={"list-unsubscribe": "<mailto:x@y.test>"}), ctx()).severity == "info"
    assert score(mail("Your verification code", "123456 is your code, it expires today"), ctx()).severity == "info"
    assert score(mail("[repo] new issue", "someone opened an issue", sender="GitHub <noreply@github.com>"), ctx()).severity == "info"


def test_the_boss_with_a_deadline_is_urgent_but_the_same_sender_saying_thanks_is_not():
    c = ctx(vips=("boss@acme.test",), known=lambda s: True, in_thread=lambda t: True)
    urgent = score(mail("Report", "Can you send the report by tomorrow morning? Need it asap.", sender="Boss <boss@acme.test>"), c)
    assert urgent.severity == "urgent" and urgent.action == "reply"
    thanks = score(mail("Re: Report", "Sounds good, thanks!", sender="Boss <boss@acme.test>"), c)
    assert thanks.severity in ("info", "notable")  # who it is from never makes it matter by itself


def test_an_unfamiliar_sender_can_still_be_critical():
    v = score(mail("Agreement for signature", "Please sign the agreement by Friday so we can proceed."), ctx())
    assert v.severity in ("important", "urgent") and v.action in ("decide", "reply")


def test_a_demand_for_money_that_fails_the_sender_checks_is_flagged_not_trusted():
    v = score(mail("FINAL NOTICE: payment overdue", "Pay now or your account will be suspended today. Invoice attached.", sender="Billing <billing@acme-pay.test>",
                   headers={"authentication-results": "mx.google.com; spf=fail; dkim=fail; dmarc=fail"}), ctx())
    assert v.severity == "notable" and v.action == "check" and any("cannot be verified" in r.lower() or "failed" in r.lower() for r in v.reasons)


def test_account_security_notices_are_worth_a_look():
    v = score(mail("Security alert: new sign-in", "A new sign-in to your account from a Windows device."), ctx())
    assert v.severity in ("notable", "important") and v.action == "check"


def test_a_meeting_change_is_news_but_not_an_emergency():
    assert score(mail("Updated invitation: standup", "The standup moved to 3pm"), ctx()).severity == "notable"


def test_your_own_topics_raise_it_and_spam_never_does():
    c = ctx(topics=("acme rollout",))
    assert score(mail("acme rollout status?", "Where are we on the acme rollout? Need an update."), c).severity in ("notable", "important")
    assert score(mail("URGENT invoice overdue", "pay now", labels=("SPAM",)), ctx()).severity == "info"


def test_it_says_how_sure_it_is_and_flags_the_borderline():
    clear = score(mail("Newsletter", "Our weekly digest", labels=("INBOX", "CATEGORY_PROMOTIONS")), ctx())
    unclear = score(mail("Quick question", "Do you have a minute?"), ctx())
    assert clear.confidence > unclear.confidence
    assert 0.0 <= unclear.confidence <= 1.0 and isinstance(unclear.reasons, list)
