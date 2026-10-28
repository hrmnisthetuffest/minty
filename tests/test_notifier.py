"""One place that decides who is told what, when and how: severity, never twice for the same news, quiet hours, private detail, retries."""
import pytest

from minty_core.notifier import Channel, Event, Notifier, Policy

NIGHT = 23 * 3600  # seconds since the epoch day start: 23:00 is inside 22:00-07:00
NOON = 12 * 3600


class Clock:
    def __init__(self, t=NOON):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Rig:
    def __init__(self, tmp_path, policy=None, clock=None, failing=()):
        self.clock = clock or Clock()
        self.sent = {name: [] for name in ("record", "digest", "panel", "say", "phone")}
        self.failing = set(failing)

        def make(name, loud, private):
            def send(event, text):
                if name in self.failing:
                    raise RuntimeError(name + " is down")
                self.sent[name].append((event.key, text))
            return Channel(name, send, loud=loud, private=private)

        self.notifier = Notifier(tmp_path / "notifier.db", [make("record", False, True), make("digest", False, True), make("panel", False, True),
                                                           make("say", True, True), make("phone", True, False)],
                                 policy or Policy(), clock=self.clock, hour_of=lambda t: int(t % 86400 // 3600))


def ev(key="t1", severity="important", fingerprint="a", **kw):
    base = dict(source="gmail:me", key=key, severity=severity, title="Mail from Alex: invoice due Friday", body="Alex wants the invoice paid by Friday.",
                action="Pay it or reply", fingerprint=fingerprint)
    base.update(kw)
    return Event(**base)


def test_what_is_only_information_is_recorded_and_nobody_is_told(tmp_path):
    rig = Rig(tmp_path)
    out = rig.notifier.publish(ev(severity="info"))
    assert out.delivered == ["record"] and rig.sent["panel"] == rig.sent["say"] == rig.sent["phone"] == []


def test_an_important_event_reaches_the_screen_the_voice_and_the_phone_once(tmp_path):
    rig = Rig(tmp_path)
    out = rig.notifier.publish(ev())
    assert set(out.delivered) == {"record", "panel", "say", "phone"}
    assert len(rig.sent["phone"]) == 1 and "invoice due Friday" in rig.sent["phone"][0][1]
    again = rig.notifier.publish(ev())  # nothing changed
    assert again.delivered == [] and again.suppressed == "already told" and len(rig.sent["phone"]) == 1


def test_a_meaningful_change_or_a_higher_severity_is_news_again(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev(severity="notable", fingerprint="a"))
    assert rig.sent["say"] == []  # notable: the screen and the digest, not the voice
    rig.notifier.publish(ev(severity="important", fingerprint="a"))  # same facts, but now more serious
    assert len(rig.sent["say"]) == 1
    rig.notifier.publish(ev(severity="important", fingerprint="b", title="Mail from Alex: the deadline moved to Thursday"))
    assert len(rig.sent["say"]) == 2 and rig.sent["say"][1][1].startswith("Update:")
    rig.notifier.publish(ev(severity="notable", fingerprint="c"))  # a change, but less serious than what was said: recorded, not repeated
    assert len(rig.sent["say"]) == 2


def test_quiet_hours_hold_what_would_make_a_sound_but_the_screen_and_digest_still_get_it(tmp_path):
    rig = Rig(tmp_path, clock=Clock(NIGHT))
    out = rig.notifier.publish(ev())
    assert out.held and "say" not in out.delivered and "phone" not in out.delivered and {"record", "digest", "panel"} <= set(out.delivered)
    assert rig.sent["say"] == [] and rig.sent["phone"] == []


def test_urgent_breaks_the_quiet_only_when_the_policy_says_so(tmp_path):
    rig = Rig(tmp_path, clock=Clock(NIGHT))
    assert rig.notifier.publish(ev(severity="urgent", key="u1")).held
    rig = Rig(tmp_path / "b", policy=Policy(urgent_bypass_quiet=True), clock=Clock(NIGHT))
    out = rig.notifier.publish(ev(severity="urgent"))
    assert not out.held and {"say", "phone"} <= set(out.delivered)
    assert rig.notifier.publish(ev(severity="important", key="i1")).held  # only urgent bypasses


def test_a_burst_cannot_become_a_flood_but_urgent_is_never_dropped(tmp_path):
    rig = Rig(tmp_path, policy=Policy(max_important_per_hour=2, max_urgent_per_hour=5))
    outs = [rig.notifier.publish(ev(key=f"k{i}")) for i in range(4)]
    assert [bool(o.delivered and "say" in o.delivered) for o in outs] == [True, True, False, False]
    assert all("digest" in o.delivered for o in outs[2:])  # over the cap: it waits for the digest, it is not lost
    assert "say" in rig.notifier.publish(ev(key="u", severity="urgent")).delivered
    rig.clock.advance(3700)
    assert "say" in rig.notifier.publish(ev(key="k9")).delivered  # the hour has passed


def test_private_detail_stays_on_private_channels(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev(detail_private=True, body="Account number 1234 is overdrawn"))
    assert "1234" in rig.sent["panel"][0][1] and "1234" not in rig.sent["phone"][0][1]
    assert "details are on the laptop" in rig.sent["phone"][0][1].lower() and "invoice due Friday" in rig.sent["phone"][0][1]  # the title is not private


def test_a_failed_channel_is_tried_again_and_does_not_stop_the_others(tmp_path):
    rig = Rig(tmp_path, failing={"phone"})
    out = rig.notifier.publish(ev())
    assert "panel" in out.delivered and "phone" not in out.delivered and out.failed == ["phone"]
    rig.failing.clear()
    assert rig.notifier.retry_due() == 0  # too soon
    rig.clock.advance(120)
    assert rig.notifier.retry_due() == 1 and len(rig.sent["phone"]) == 1
    assert rig.notifier.retry_due() == 0  # done, not repeated


def test_a_channel_that_keeps_failing_is_given_up_on_not_hammered(tmp_path):
    rig = Rig(tmp_path, failing={"phone"})
    rig.notifier.publish(ev())
    for _ in range(10):
        rig.clock.advance(4000)
        rig.notifier.retry_due()
    assert rig.notifier.attempts("gmail:me", "t1", "phone") == 3  # three tries, then it stops and says so
    assert rig.notifier.failed()[0][2] == "phone"


def test_an_urgent_event_nobody_dealt_with_is_said_again_a_couple_of_times_then_left_alone(tmp_path):
    rig = Rig(tmp_path, policy=Policy(remind_after_h=4, max_reminders=2))
    rig.notifier.publish(ev(severity="urgent"))
    for _ in range(5):
        rig.clock.advance(4.5 * 3600)
        rig.notifier.remind_due()
    assert len(rig.sent["say"]) == 3  # the first telling and two reminders
    assert all(text.startswith("Reminder:") for _, text in rig.sent["say"][1:])


def test_resolving_an_event_stops_its_reminders(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev(severity="urgent"))
    rig.notifier.resolve("gmail:me", "t1")
    rig.clock.advance(5 * 3600)
    assert rig.notifier.remind_due() == 0 and len(rig.sent["say"]) == 1


def test_text_is_one_plain_line_with_no_links_or_hidden_characters(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev(title="Pay now https://evil.test/p?x=1‮ and\nreply", body="see http://x.test"))
    _, text = rig.sent["say"][0]
    assert "http" not in text and "‮" not in text and "\n" not in text and "[link]" in text


def test_what_was_told_survives_a_restart(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev())
    rig.notifier.close()
    again = Rig(tmp_path)
    assert again.notifier.publish(ev()).suppressed == "already told"


def test_the_record_lists_what_happened_for_you_to_look_at(tmp_path):
    rig = Rig(tmp_path)
    rig.notifier.publish(ev(key="a", severity="urgent"))
    rig.notifier.publish(ev(key="b", severity="info", title="Newsletter"))
    rows = rig.notifier.recent(10)
    assert [r["key"] for r in rows] == ["b", "a"] and rows[1]["severity"] == "urgent"
