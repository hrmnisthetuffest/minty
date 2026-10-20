"""File relay between two machines' bots: off by default, allow-listed peers, loops cut at hop 2 (W3-15)."""
import json
import os
import stat
from pathlib import Path

from tests.telegram_fake import FakeTelegram
from tests.test_telegram_gateway import OWNER, Agent, build
from minty_core.secretscan import SecretScanner
from minty_core.telegram.relay import MAX_HOP, Relay

PEER_PC, PEER_OTHER = 555000111, 555000222
SECRET = "gsk_" + "a1B2c3D4e5F6g7H8i9J0" * 2


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def setup(tmp_path, enabled=True, peers=None, machine="laptop"):
    agent = Agent()
    gw, fake, store, _ = build(tmp_path, agent=agent)
    gw.machine = machine
    data = store.config.model_dump(mode="json")
    data["telegram"]["relay"] = enabled
    data["telegram"]["relay_peers"] = peers if peers is not None else {"pc": PEER_PC}
    store.update(data)
    roots = tmp_path / "ws"
    roots.mkdir()
    clock = Clock()
    relay = Relay(gw, tmp_path / "relay-in", SecretScanner(), send_roots=lambda: [roots], clock=clock)
    relay.install()
    return gw, fake, relay, roots, agent, clock


def envelope(to="laptop", frm="pc", hop=0, id_="r1", intent="file"):
    return "MINTY-RELAY/1 " + json.dumps({"from": frm, "to": to, "id": id_, "hop": hop, "intent": intent})


def arrival(fake, caption, peer=PEER_PC, data=b"hello file", name="report.pdf", size=None):
    fake.add_file("rf", data, "documents/rf.bin")
    return fake.say(peer, "", caption=caption, document={"file_id": "rf", "file_name": name,
                                                          "file_size": size if size is not None else len(data)},
                    **{"from": {"id": peer, "is_bot": True, "first_name": "pc bot"}})


async def test_a_file_from_the_pc_bot_is_quarantined_and_you_are_told_once(tmp_path):
    gw, fake, relay, _, agent, _ = setup(tmp_path)
    await gw.handle_update(arrival(fake, envelope()))
    saved = list((tmp_path / "relay-in").iterdir())
    assert len(saved) == 1 and saved[0].read_bytes() == b"hello file" and stat.S_IMODE(saved[0].stat().st_mode) == 0o600
    notice = fake.texts(OWNER)
    assert len(notice) == 1 and "report.pdf" in notice[0] and "pc" in notice[0] and "didn't open it" in notice[0]
    assert agent.asked == []  # nothing in the file or the message is acted on


async def test_it_does_nothing_until_it_is_switched_on_here(tmp_path):
    gw, fake, relay, _, agent, _ = setup(tmp_path, enabled=False)
    await gw.handle_update(arrival(fake, envelope()))
    assert not (tmp_path / "relay-in").exists() and fake.sent == [] and "getFile" not in fake.calls
    sent = await relay.send_file("pc", Path("anything"))
    assert "off" in sent and fake.media == []


async def test_an_unknown_bot_or_the_wrong_target_is_dropped_silently(tmp_path):
    gw, fake, relay, _, _, _ = setup(tmp_path)
    await gw.handle_update(arrival(fake, envelope(), peer=PEER_OTHER))
    await gw.handle_update(arrival(fake, envelope(to="tablet", id_="r2")))
    await gw.handle_update(arrival(fake, "just a caption, no envelope", ))
    assert fake.sent == [] and "getFile" not in fake.calls


async def test_a_message_that_already_made_two_hops_is_cut(tmp_path):
    gw, fake, relay, _, _, _ = setup(tmp_path)
    assert MAX_HOP == 2
    await gw.handle_update(arrival(fake, envelope(hop=2)))
    await gw.handle_update(arrival(fake, envelope(hop=7, id_="r9")))
    assert fake.sent == [] and "getFile" not in fake.calls
    await gw.handle_update(arrival(fake, envelope(hop=1, id_="r3")))
    assert len(fake.texts(OWNER)) == 1  # one hop in is fine


async def test_an_ack_is_never_answered_so_two_bots_cannot_ping_pong(tmp_path):
    gw, fake, relay, _, _, _ = setup(tmp_path)
    ack = fake.say(PEER_PC, "", caption=envelope(intent="ack", hop=1, id_="a1"),
                   **{"from": {"id": PEER_PC, "is_bot": True}})
    await gw.handle_update(ack)
    assert fake.media == [] and fake.calls.count("sendMessage") == 0 and fake.calls.count("sendDocument") == 0
    # and a received file sends one ack back with the hop increased
    await gw.handle_update(arrival(fake, envelope(id_="f1")))
    acks = [m for m in fake.sent if m["chat_id"] == PEER_PC]
    assert len(acks) == 1
    sent_env = json.loads(acks[0]["text"].split(" ", 1)[1])
    assert sent_env["intent"] == "ack" and sent_env["hop"] == 1 and sent_env["to"] == "pc"


async def test_the_same_request_id_is_handled_once_and_a_flood_is_limited(tmp_path):
    gw, fake, relay, _, _, clock = setup(tmp_path)
    await gw.handle_update(arrival(fake, envelope(id_="same")))
    await gw.handle_update(arrival(fake, envelope(id_="same")))
    assert len(list((tmp_path / "relay-in").iterdir())) == 1
    for i in range(10):
        await gw.handle_update(arrival(fake, envelope(id_=f"n{i}")))
    assert len(list((tmp_path / "relay-in").iterdir())) == 5  # at most 5 a minute per peer
    clock.t += 61
    await gw.handle_update(arrival(fake, envelope(id_="later")))
    assert len(list((tmp_path / "relay-in").iterdir())) == 6


async def test_a_file_over_the_download_cap_is_refused_with_a_note(tmp_path):
    gw, fake, relay, _, _, _ = setup(tmp_path)
    await gw.handle_update(arrival(fake, envelope(), size=30 * 1024 * 1024))
    assert "getFile" not in fake.calls and "too big" in fake.texts(OWNER)[0]


async def test_sending_is_limited_to_your_workspace_and_never_a_secret_or_a_link(tmp_path):
    gw, fake, relay, roots, _, _ = setup(tmp_path)
    ok = roots / "notes.txt"
    ok.write_text("plain notes")
    assert "Sent" in await relay.send_file("pc", ok)
    sent = fake.media[-1]
    env = json.loads(sent["caption"].split(" ", 1)[1])
    assert sent["method"] == "sendDocument" and sent["chat_id"] == PEER_PC and sent["filename"] == "notes.txt"
    assert env["from"] == "laptop" and env["to"] == "pc" and env["hop"] == 0 and env["intent"] == "file"
    outside = tmp_path / "diary.txt"
    outside.write_text("private")
    assert "only" in await relay.send_file("pc", outside)
    link = roots / "link.txt"
    link.symlink_to(outside)
    assert "only" in await relay.send_file("pc", link)
    leaky = roots / "env.txt"
    leaky.write_text(f"KEY={SECRET}")
    assert "key" in (await relay.send_file("pc", leaky)).lower() and len(fake.media) == 1
    assert "don't know" in await relay.send_file("tablet", ok)


async def test_the_owner_command_sends_a_file_by_name(tmp_path):
    gw, fake, relay, roots, _, _ = setup(tmp_path)
    (roots / "plan.md").write_text("# plan")
    await gw.handle_update(fake.say(OWNER, "/relay pc plan.md"))
    assert fake.media and fake.media[0]["filename"] == "plan.md"
    assert "Sent" in fake.texts(OWNER)[-1]
