"""WP-15 acceptance: the three promises that matter most.

1. The microphone is never kept outside a session you started (checked through the real pipeline).
2. A recorded session becomes a note with a summary, action items and the consent reminder, and the audio is gone.
3. Deleting a person deletes what was derived from them, everywhere."""
import asyncio
from datetime import date, timedelta

from minty_core.config import Config
from minty_core.productivity.kit import ProductivityProvider
from minty_core.vault.kit import VaultProvider
from tests.plugin_helpers import make_host
from tests.test_pipeline import build, finish


def provider_for(tmp_path, **services):
    cfg = Config()
    cfg.productivity.enabled = True
    cfg.recorder.enabled = True
    cfg.vault.enabled = True
    events = []
    services = {"vault": VaultProvider(lambda: cfg, home=tmp_path), **services}
    return ProductivityProvider(lambda: cfg, tmp_path / "cfg", services), cfg, events


async def test_the_real_pipeline_keeps_no_audio_unless_you_started_a_recording(tmp_path):
    provider, _cfg, _events = provider_for(tmp_path)
    recorder = provider.recorder()
    p, f = build(tmp_path / "pcfg")
    p.recorder = recorder
    f.audio.push(*([b"\x02\x00" * 320] * 30))
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.15)
    assert not list(tmp_path.rglob("*.wav"))  # idle and awake: not a byte kept
    provider.start_recording("lecture", lambda *a, **k: None)
    f.audio.push(*([b"\x02\x00" * 320] * 30))
    await asyncio.sleep(0.15)
    session = recorder.stop()
    await finish(p, f, task)
    assert sum(c.stat().st_size for c in session.chunks) > 30 * 640 - 1  # kept while you had it on
    after = sum(c.stat().st_size for c in session.chunks)
    f.audio.push(*([b"\x02\x00" * 320] * 10))
    assert sum(c.stat().st_size for c in session.chunks) == after  # and not a byte since you stopped


async def test_a_recorded_session_becomes_a_vault_note_with_summary_actions_and_consent(tmp_path):
    async def transcribe(pcm, rate):
        return ("Today we studied refraction. Light bends when it enters water. "
                "Remember to revise Snell's law before friday. Please bring the lab report on monday.")

    sent = []
    provider, _cfg, _e = provider_for(tmp_path, transcribe=transcribe)
    publish = lambda kind, **f: sent.append((kind, f))  # noqa: E731
    reminder = provider.start_recording("Physics class", publish)
    assert "everyone" in reminder
    provider.recorder().feed(b"\x01\x00" * 16000)
    provider.recorder().mark("Snell's law")
    folder_of_audio = provider.recorder().session.dir
    assert "making the notes" in provider.stop_recording(publish)
    for _ in range(200):
        await asyncio.sleep(0.02)
        if any(k == "notify" for k, _ in sent):
            break
    assert any(k == "notify" and "ready" in f["text"] for k, f in sent), sent
    notes = [n for n in provider._services["vault"].kit().vault.list(None, limit=50) if "recording" in n.tags]
    assert len(notes) == 1
    note = notes[0]
    assert note.folder == "sources" and note.confidence == "untrusted"  # speech heard is outside content
    body = note.body
    assert "## Summary" in body and "## Action items" in body and "revise Snell's law before friday" in body
    assert "## Marked moments" in body and "## Consent" in body and "agree" in body.lower()
    assert not folder_of_audio.exists()  # the audio is deleted once the transcript exists
    assert ("recording", {"active": False, "label": "Physics class"}) in sent  # the HUD marker goes off


async def test_deleting_a_person_removes_everything_derived_from_them(tmp_path):
    h = make_host(tmp_path / "cfg", services={"publish": lambda *a, **k: None})
    data = h._store.config.model_dump(mode="json")
    data["productivity"]["enabled"] = True
    data["butler"]["enabled"] = True
    h._store.update(data)
    soon = (date.today() + timedelta(days=3)).strftime("%m-%d")
    assert (await h.call("productivity_person", {"action": "add", "name": "Sam Taylor", "birthday": soon})
            ).startswith("Noted")
    await h.call("productivity_person", {"action": "fact", "name": "Sam", "text": "loves mangoes"})
    await h.call("butler_add", {"domain": "waiting", "name": "Sam to send the notes", "who": "Sam"})
    await h.call("butler_add", {"domain": "waiting", "name": "Aman to return the book", "who": "Aman"})
    assert "Sam" in await h.call("productivity_day", {"kind": "plan"})  # her birthday is coming up
    assert "Deleted them" in await h.call("productivity_person", {"action": "delete", "name": "Sam"})
    assert "I don't know that person." == await h.call("productivity_person", {"action": "show", "name": "Sam"})
    assert "Sam" not in await h.call("productivity_day", {"kind": "plan"})
    waiting = await h.call("butler_list", {"domain": "waiting"})
    assert "Sam" not in waiting and "return the book" in waiting  # hers went, someone else's stayed
