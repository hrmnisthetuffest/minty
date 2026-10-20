"""Gateway core: allow-list, pairing, routing, label, rate limit (W3-02)."""
import asyncio
from types import SimpleNamespace

from tests.telegram_fake import TOKEN, FakeTelegram
from minty_core.config import ConfigStore
from minty_core.telegram.api import BotAPI
from minty_core.telegram.gateway import PairingStore, Sender, TelegramGateway, chunks

OWNER, STRANGER = 1111, 2222


class Agent:
    def __init__(self, reply="It's noon."):
        self.reply, self.asked, self.history = reply, [], []

    async def respond(self, text, **kw):
        self.asked.append(text)
        yield self.reply


def build(tmp_path, allowed=(OWNER,), agent=None, private=False):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["telegram"]["allowed_ids"] = list(allowed)
    store.update(data)
    fake = FakeTelegram()
    bot = BotAPI(TOKEN, fake.client)
    pairing = PairingStore(tmp_path / "pairing.json")
    sender = Sender(bot, interval=0.0)
    gw = TelegramGateway(bot, store, agent or Agent(), machine="laptop", pairing=pairing, sender=sender,
                         private_now=lambda: private)
    return gw, fake, store, pairing


async def test_an_allowed_chat_gets_an_answer_with_the_machine_label(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    await gw.handle_update(fake.say(OWNER, "what time is it"))
    assert fake.texts(OWNER) == ["[laptop] It's noon."] and gw.agent.asked == ["what time is it"]


async def test_everyone_else_is_dropped_silently(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    await gw.handle_update(fake.say(STRANGER, "hello?"))
    await gw.handle_update(fake.say(STRANGER, "/status"))
    await gw.handle_update(fake.press(STRANGER, "ap:1:abcd"))
    assert fake.sent == [] and fake.answers == [] and gw.agent.asked == []


async def test_groups_and_forged_senders_are_never_answered(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    group = fake.say(OWNER, "hi")
    group["message"]["chat"] = {"id": -100123, "type": "supergroup"}
    await gw.handle_update(group)
    forged = fake.say(OWNER, "hi")
    forged["message"]["from"] = {"id": STRANGER}
    await gw.handle_update(forged)
    assert fake.sent == []


async def test_pairing_with_the_right_code_adds_the_chat_once(tmp_path):
    gw, fake, store, pairing = build(tmp_path, allowed=())
    code = pairing.new_code()
    await gw.handle_update(fake.say(STRANGER, "/pair 000000"))
    assert fake.sent == [] and STRANGER not in gw.allowed  # a wrong code: silence
    await gw.handle_update(fake.say(STRANGER, f"/pair {code}"))
    assert STRANGER in gw.allowed and "Paired" in fake.texts(STRANGER)[0]
    assert ConfigStore(tmp_path).load().telegram.allowed_ids == [STRANGER]  # saved
    await gw.handle_update(fake.say(3333, f"/pair {code}"))
    assert 3333 not in gw.allowed  # the code was used up


def test_a_pairing_code_expires_and_locks_after_five_wrong_tries(tmp_path):
    clock = {"t": 1000.0}
    p = PairingStore(tmp_path / "p.json", lambda: clock["t"])
    code = p.new_code()
    assert len(code) == 6 and code.isdigit()
    assert "hash" in (tmp_path / "p.json").read_text() and code not in (tmp_path / "p.json").read_text()
    for _ in range(5):
        assert p.check("999999") is False
    assert p.check(code) is False  # locked out
    code2 = p.new_code()
    clock["t"] += 601
    assert p.check(code2) is False  # expired
    code3 = p.new_code()
    assert p.check(code3) is True and p.check(code3) is False


async def test_private_mode_means_nothing_is_sent_back(tmp_path):
    gw, fake, _, _ = build(tmp_path, private=True)
    await gw.handle_update(fake.say(OWNER, "what's my blood pressure"))
    assert "private mode" in fake.texts(OWNER)[0] and gw.agent.asked == []


async def test_a_private_answer_stays_on_the_laptop(tmp_path):
    from minty_core.llm.types import Message

    class Leaky(Agent):
        async def respond(self, text, **kw):
            self.history.append(Message("user", text))
            self.history.append(Message("tool", "secret health data", private=True))
            yield "Your resting heart rate is 61."

    gw, fake, _, _ = build(tmp_path, agent=Leaky())
    await gw.handle_update(fake.say(OWNER, "how is my heart rate"))
    assert fake.texts(OWNER) == ["[laptop] That answer is private, so I kept it for the laptop."]


async def test_fifty_events_are_sent_no_faster_than_one_a_second_per_chat():
    fake = FakeTelegram()
    clock = {"t": 0.0}
    slept = []

    async def sleep(s):
        slept.append(s)
        clock["t"] += s

    sender = Sender(BotAPI(TOKEN, fake.client), interval=1.0, clock=lambda: clock["t"], sleep=sleep)
    await asyncio.gather(*(sender.send(OWNER, f"event {i}") for i in range(50)))
    assert len(fake.sent) == 50 and sum(slept) >= 49.0  # 50 sends need at least 49 one-second gaps
    other = FakeTelegram()
    sender2 = Sender(BotAPI(TOKEN, other.client), interval=1.0, clock=lambda: 0.0, sleep=sleep)
    await sender2.send(1, "a")
    await sender2.send(2, "b")  # a different chat is not held back
    assert len(other.sent) == 2


def test_long_text_is_split_at_sentence_ends():
    text = ("Sentence number one is here. " * 300).strip()
    parts = chunks(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and len(parts) >= 8 and " ".join(parts).count("Sentence") == 300


async def test_unknown_commands_are_said_plainly(tmp_path):
    gw, fake, _, _ = build(tmp_path)
    await gw.handle_update(fake.say(OWNER, "/dance"))
    assert "I don't know /dance" in fake.texts(OWNER)[0] and gw.agent.asked == []
    assert SimpleNamespace is not None


def test_the_machine_name_is_in_the_traces_and_the_receipts(tmp_path):
    from minty_core.receipts import ReceiptStore
    from minty_core.trace import Tracer, read_events
    Tracer(tmp_path / "t", static={"machine": "laptop"}).event("turn", words=3)
    assert read_events(tmp_path / "t")[0]["machine"] == "laptop"
    store = ReceiptStore(tmp_path / "r.sqlite", machine="laptop")
    store.record("files_write", 1, "write a")
    assert store.recent(1)[0].machine == "laptop"
    old = tmp_path / "old.sqlite"
    import sqlite3
    db = sqlite3.connect(old)  # a receipts file made before the column existed
    db.execute("create table receipts (id integer primary key autoincrement, ts real, turn_id text, tool text,"
               " risk integer, summary text, request text, approval_id integer, outcome text, verified text,"
               " detail text)")
    import time
    db.execute("insert into receipts(ts, tool, risk, summary, outcome) values (?, 't', 1, 's', 'ok')", (time.time(),))
    db.commit()
    db.close()
    assert ReceiptStore(old, machine="x").recent(1)[0].machine == ""  # upgraded in place, old rows kept
