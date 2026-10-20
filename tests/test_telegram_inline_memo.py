"""Inline search of notes and memory from any chat (739); a voice memo is saved, not acted on (738)."""
import numpy as np

from tests.test_telegram_gateway import OWNER, STRANGER, build
from minty_core.audio.types import Audio
from minty_core.gists import GistIndex
from minty_core.telegram.inline import InlineSearch
from minty_core.telegram.media import pcm_to_ogg
from minty_core.telegram.voice import ChatPrefs, VoiceNotes


class Host:
    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == "journal_search_entries":
            return "2026-10-12: Shoe size is 9 UK\n2026-10-13: Bought new running shoes"
        return "Saved to your journal."


def inline(fake, who, query):
    return fake._push({"inline_query": {"id": f"iq{fake._next_update}", "from": {"id": who}, "query": query,
                                        "offset": ""}})


def with_search(tmp_path, private=False):
    gw, fake, store, _ = build(tmp_path, private=private)
    host, gists = Host(), GistIndex(tmp_path / "memory.db")
    gists.add("fact", "Alex's shoe size is 9 UK", title="shoe size", category="personal")
    gists.add("fact", "Birthday is in September", title="birthday", category="personal")
    InlineSearch(gw, host.call, gists).install()
    return gw, fake, host


async def test_the_owner_gets_note_and_memory_hits_as_inline_results(tmp_path):
    gw, fake, host = with_search(tmp_path)
    await gw.handle_update(inline(fake, OWNER, "shoe"))
    answer = fake.inline_answers[0]
    titles = [r["title"] for r in answer["results"]]
    assert answer["is_personal"] is True and any("shoe" in t.lower() for t in titles)
    assert all(r["type"] == "article" and r["input_message_content"]["message_text"] for r in answer["results"])
    assert len({r["id"] for r in answer["results"]}) == len(answer["results"])
    assert any("Alex's shoe size" in r["input_message_content"]["message_text"] for r in answer["results"])


async def test_a_stranger_gets_nothing_and_a_one_letter_query_dumps_nothing(tmp_path):
    gw, fake, host = with_search(tmp_path)
    await gw.handle_update(inline(fake, STRANGER, "shoe"))
    assert fake.inline_answers == [] and host.calls == []
    await gw.handle_update(inline(fake, OWNER, "s"))
    assert fake.inline_answers[0]["results"] == [] and host.calls == []


async def test_a_private_day_answers_with_nothing(tmp_path):
    gw, fake, host = with_search(tmp_path, private=True)
    await gw.handle_update(inline(fake, OWNER, "shoe"))
    assert fake.inline_answers[0]["results"] == [] and host.calls == []


async def test_polling_asks_for_inline_queries(tmp_path):
    gw, fake, _ = with_search(tmp_path)
    seen = []
    original = gw.api.call

    async def spy(method, *a, **kw):
        if method == "getUpdates":
            seen.append(kw.get("allowed_updates"))
        return await original(method, *a, **kw)

    gw.api.call = spy
    await gw.api.get_updates(None, timeout_s=0)
    assert "inline_query" in seen[0]


# ---- voice memo ------------------------------------------------------------------------------------------------
class STT:
    async def transcribe(self, pcm, rate=16000):
        return "buy a new notebook for physics"


class TTS:
    async def synthesize(self, text):
        return Audio(b"\x00\x00" * 2000, 22050)


def memo_setup(tmp_path):
    gw, fake, store, _ = build(tmp_path)
    saved = []

    async def save(text):
        saved.append(text)
        return "Saved to your notes."

    VoiceNotes(gw, STT(), lambda: TTS(), ChatPrefs(tmp_path / "chats.json"), memo=save).install()
    return gw, fake, saved


def note(fake, caption=""):
    t = np.arange(8000) / 16000
    data = pcm_to_ogg((np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16).tobytes(), 16000)
    fake.add_file("v1", data, "voice/1.oga")
    return fake.say(OWNER, "", caption=caption, voice={"file_id": "v1", "duration": 1, "file_size": len(data)})


async def test_a_voice_note_captioned_memo_is_saved_not_acted_on(tmp_path):
    gw, fake, saved = memo_setup(tmp_path)
    await gw.handle_update(note(fake, caption="memo"))
    assert saved == ["buy a new notebook for physics"] and gw.agent.asked == []
    assert "Saved" in fake.texts(OWNER)[-1] and fake.media == []


async def test_memo_mode_saves_every_note_until_it_is_turned_off(tmp_path):
    gw, fake, saved = memo_setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "/memo on"))
    await gw.handle_update(note(fake))
    await gw.handle_update(note(fake))
    assert len(saved) == 2 and gw.agent.asked == []
    await gw.handle_update(fake.say(OWNER, "/memo off"))
    await gw.handle_update(note(fake))
    assert len(saved) == 2 and gw.agent.asked == ["buy a new notebook for physics"]
