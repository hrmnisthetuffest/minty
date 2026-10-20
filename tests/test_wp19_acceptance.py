"""WP-19 acceptance (plan-master):
* every summary cites its source, and a removed source flags the notes that depend on it;
* media outputs never overwrite originals."""
import os
import shutil

import pytest

from tests.test_create_media_notes import LECTURE, needs_ffmpeg, sha, tone
from tests.test_create_plugin import Rig


async def test_every_kind_of_summary_names_where_it_came_from(tmp_path):
    r = Rig(tmp_path)
    for url in ("https://sci.example/feed", "https://tech.example/feed"):
        await r.call("news", {"action": "add_feed", "url": url})
    brief = await r.call("news", {"action": "brief"})
    items = [ln for ln in brief.splitlines() if ln[:2].strip(". ").isdigit() or ln[:1].isdigit()]
    assert items and all(("https://" in ln) and (" — " in ln) for ln in items)  # an outlet and a link on every item
    ev = await r.call("evidence", {"claim": "The Great Wall of China is 21,196 km long"})
    assert all("https://" in ln for ln in ev.splitlines() if ln[:2] in ("1.", "2.", "3."))
    notes = await r.call("media", {"action": "notes", "text": "A talk about plants and light. " * 20, "title": "Plants"})
    assert "Source: pasted text" in notes


@needs_ffmpeg
async def test_a_note_made_from_a_file_is_flagged_when_that_file_goes_away_or_changes(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    talk, other = tone(media / "talk.wav", 4), tone(media / "other.wav", 3, 880)
    r = Rig(tmp_path)

    async def transcribe(pcm, rate):
        return LECTURE[0][1]

    r.host._services["transcribe"] = transcribe
    await r.call("media", {"action": "notes", "path": str(talk), "title": "Talk notes"})
    await r.call("media", {"action": "notes", "path": str(other), "title": "Other notes"})
    assert "still there and unchanged" in await r.call("sources", {"action": "audit"})
    other.write_bytes(other.read_bytes()[:-200] + b"\x01" * 200)  # edited in place
    talk.unlink()
    audit = await r.call("sources", {"action": "audit"})
    assert "Talk notes: source removed: talk.wav" in audit and "Other notes: source changed: other.wav" in audit


@needs_ffmpeg
async def test_no_media_tool_ever_overwrites_or_alters_an_original(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    a, b = tone(media / "a.wav", 4), tone(media / "b.wav", 4, 660)
    hashes = {p: sha(p) for p in (a, b)}
    # outputs deliberately aimed at the media folder itself, and named like the originals
    r = Rig(tmp_path, folder=str(media))
    for name in ("a", "a", "b"):
        await r.call("media", {"action": "trim", "path": str(a), "start": "0", "end": "2", "name": name})
    await r.call("media", {"action": "merge", "paths": [str(a), str(b)], "name": "a"})
    assert {p: sha(p) for p in (a, b)} == hashes
    names = sorted(p.name for p in (media / "media").iterdir())  # outputs go under <folder>/media
    assert len(names) == len(set(names)) == 4 and all(n.endswith(".wav") for n in names)
    assert not (media / "media" / "a.wav").samefile(a)
