"""639: the notification reader — opt-in per app, memory only, speaks when asked."""
import asyncio
import json

from minty_core.system.notify import NotificationReader, monitor, parse_notify

MSG = '''method call time=1760770000.123 sender=:1.88 -> destination=org.freedesktop.Notifications serial=12 path=/org/freedesktop/Notifications; interface=org.freedesktop.Notifications; member=Notify
   string "{app}"
   uint32 0
   string "icon-name"
   string "{title}"
   string "{body}"
   array [
      string "default"
      string "Open"
   ]
   array [
      dict entry(
         string "urgency"
         variant             byte 1
      )
   ]
   int32 -1
'''


def msg(app="Telegram Desktop", title="Ravi", body="are you coming?"):
    return MSG.format(app=app, title=title, body=body)


def test_a_notify_call_gives_app_title_and_body_and_nothing_nested():
    n = parse_notify(msg())
    assert (n.app, n.title, n.body) == ("Telegram Desktop", "Ravi", "are you coming?")
    assert "Open" not in (n.title + n.body)


def test_other_dbus_traffic_is_ignored():
    assert parse_notify("signal time=1 sender=:1.2 -> destination=(null destination) serial=3 path=/x; member=Foo\n") is None
    assert parse_notify("") is None


def test_only_opted_in_apps_are_kept_and_the_rest_are_dropped_at_once():
    apps = ["telegram"]
    r = NotificationReader(lambda: apps, clock=lambda: 100.0)
    r.feed(parse_notify(msg()))
    r.feed(parse_notify(msg(app="Firefox", title="Bank", body="Your OTP is 123456")))
    assert [n.app for n in r.pending()] == ["Telegram Desktop"]
    assert "123456" not in str([n.__dict__ for n in r.pending()])


def test_nothing_is_kept_while_no_app_is_opted_in():
    r = NotificationReader(lambda: [], clock=lambda: 0.0)
    r.feed(parse_notify(msg()))
    assert r.pending() == []


def test_reading_speaks_a_summary_and_clears_it():
    apps = ["telegram", "whatsapp"]
    r = NotificationReader(lambda: apps, clock=lambda: 0.0)
    for body in ("are you coming?", "bring the notes"):
        r.feed(parse_notify(msg(body=body)))
    r.feed(parse_notify(msg(app="WhatsApp", title="Mum", body="dinner at 8")))
    text = r.read()
    assert "3 notifications" in text and "Ravi: are you coming?" in text and "Mum: dinner at 8" in text
    assert r.read() == "No new notifications from the apps you chose."
    r.feed(parse_notify(msg()))
    assert "1 notification" in r.read(app="telegram") and r.pending() == []


def test_old_ones_expire_and_the_list_is_capped_and_bodies_are_cut():
    t = [0.0]
    r = NotificationReader(lambda: ["telegram"], clock=lambda: t[0], keep=3, ttl_s=100)
    for i in range(6):
        r.feed(parse_notify(msg(body=f"m{i}")))
    assert [n.body for n in r.pending()] == ["m3", "m4", "m5"]
    t[0] = 500.0
    assert r.pending() == []
    r.feed(parse_notify(msg(body="x" * 1000)))
    assert len(r.pending()[0].body) <= 160


def test_it_never_writes_to_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = NotificationReader(lambda: ["telegram"], clock=lambda: 0.0)
    r.feed(parse_notify(msg()))
    r.read()
    assert list(tmp_path.iterdir()) == []


class FakeProc:
    def __init__(self, text):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(text.encode())
        self.stdout.feed_eof()
        self.killed = False

    async def wait(self):
        return 0

    def kill(self):
        self.killed = True


async def test_the_monitor_splits_the_stream_into_messages_and_feeds_the_reader():
    stream = msg() + "signal time=2 sender=:1.1 -> destination=(null destination) serial=1 path=/p; member=Other\n" + \
        msg(app="WhatsApp", title="Mum", body="hi")
    seen = {}

    async def spawn(*argv, **kw):
        seen["argv"] = argv
        return FakeProc(stream)

    r = NotificationReader(lambda: ["telegram", "whatsapp"], clock=lambda: 0.0)
    await monitor(r, spawn=spawn, retry_s=0, once=True)
    assert [n.title for n in r.pending()] == ["Ravi", "Mum"]
    assert seen["argv"][0] == "dbus-monitor" and "member='Notify'" in seen["argv"][1]
