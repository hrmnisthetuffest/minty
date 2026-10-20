"""Quarantine: hash, look the hash up (never the file), release only when asked and never when flagged."""
import hashlib
import json
import os
import stat

import pytest

from minty_core.web.db import WebDB
from minty_core.web.downloads import Quarantine, VirusTotal

HELLO = hashlib.sha256(b"hello").hexdigest()


class Rig:
    def __init__(self, tmp_path, key="vt-key-123", answers=None):
        self.t = 5_000_000.0
        self.db = WebDB(tmp_path / "web.db")
        self.requests: list[tuple[str, dict]] = []
        self.answers = answers if answers is not None else {}
        self.vt = VirusTotal(lambda: key, self.get, self.db, clock=lambda: self.t)
        self.q = Quarantine(self.db, tmp_path / "quarantine", self.vt, clock=lambda: self.t)
        self.tmp = tmp_path

    async def get(self, url, headers):
        self.requests.append((url, headers))
        return self.answers.get(url.rsplit("/", 1)[-1], (404, {}))

    def download(self, name="notes.pdf", data=b"hello"):
        src = self.tmp / "incoming" / name
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(data)
        return src


def stats(mal=0, sus=0, harmless=60, undetected=10):
    return 200, {"data": {"attributes": {"last_analysis_stats": {"malicious": mal, "suspicious": sus,
                                                                 "harmless": harmless, "undetected": undetected}}}}


def test_ingest_hashes_moves_and_locks_the_file_down(tmp_path):
    r = Rig(tmp_path)
    row = r.q.ingest(r.download(), "https://school.example/notes")
    path = tmp_path / "quarantine" / os.path.basename(row["path"])
    assert row["sha256"] == HELLO and row["size"] == 5 and row["status"] == "quarantined"
    assert path.exists() and not (tmp_path / "incoming" / "notes.pdf").exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_odd_names_are_made_plain_and_cannot_escape_the_folder(tmp_path):
    r = Rig(tmp_path)
    row = r.q.ingest(r.download("saved.pdf"), "", name="../../evil name?.pdf")
    assert os.path.dirname(row["path"]) == str(tmp_path / "quarantine")
    assert row["name"] == "evil name_.pdf" and os.path.exists(row["path"])


async def test_only_the_hash_leaves_and_a_flagged_file_cannot_be_released(tmp_path):
    r = Rig(tmp_path, answers={HELLO: stats(mal=7)})
    row = r.q.ingest(r.download(), "")
    out = await r.q.lookup(row["id"])
    url, headers = r.requests[0]
    assert url == f"https://www.virustotal.com/api/v3/files/{HELLO}" and headers == {"x-apikey": "vt-key-123"}
    assert len(r.requests) == 1 and "7 of 77" in out["message"] and "malicious" in out["message"]
    with pytest.raises(ValueError, match="will not release"):
        r.q.release(row["id"], tmp_path / "Downloads")


async def test_an_unknown_file_is_said_to_be_unknown_not_safe_and_can_be_released(tmp_path):
    r = Rig(tmp_path)
    row = r.q.ingest(r.download(), "")
    out = await r.q.lookup(row["id"])
    assert out["state"] == "unknown" and "never seen" in out["message"] and "not proof" in out["message"]
    dest = r.q.release(row["id"], tmp_path / "Downloads")
    assert dest.read_bytes() == b"hello" and dest.parent == tmp_path / "Downloads"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644 and not os.access(dest, os.X_OK)
    assert r.q.get(row["id"])["status"] == "released"
    with pytest.raises(ValueError, match="already"):
        r.q.release(row["id"], tmp_path / "Downloads")


async def test_a_clean_result_reads_plainly_and_suspicious_ones_warn(tmp_path):
    r = Rig(tmp_path, answers={HELLO: stats()})
    row = r.q.ingest(r.download(), "")
    assert "none of 70" in (await r.q.lookup(row["id"]))["message"]
    r2 = Rig(tmp_path / "b", answers={HELLO: stats(sus=2)})
    row2 = r2.q.ingest(r2.download(), "")
    assert "suspicious" in (await r2.q.lookup(row2["id"]))["message"]


async def test_no_key_means_hash_only_and_no_request(tmp_path):
    r = Rig(tmp_path, key=None)
    row = r.q.ingest(r.download(), "")
    out = await r.q.lookup(row["id"])
    assert r.requests == [] and out["state"] == "nokey" and "set-key plugin.web.virustotal_key" in out["message"]


async def test_the_limits_four_a_minute_and_five_hundred_a_day(tmp_path):
    r = Rig(tmp_path)
    ids = [r.q.ingest(r.download(f"f{i}.txt", f"data{i}".encode()), "")["id"] for i in range(6)]
    states = [(await r.q.lookup(i))["state"] for i in ids]
    assert states[:4] == ["unknown"] * 4 and states[4:] == ["skipped", "skipped"] and len(r.requests) == 4
    r.t += 61
    assert (await r.q.lookup(ids[4]))["state"] == "unknown"
    r.db.set_meta("vt_day", f"{__import__('time').strftime('%Y-%m-%d', __import__('time').localtime(r.t))}:500")
    r.t += 120
    assert (await r.q.lookup(ids[5]))["state"] == "skipped" and "today" in (await r.q.lookup(ids[5]))["message"]


async def test_a_rejected_key_and_a_server_error_are_reported_not_fatal(tmp_path):
    r = Rig(tmp_path, answers={HELLO: (401, {})})
    row = r.q.ingest(r.download(), "")
    assert (await r.q.lookup(row["id"]))["state"] == "error" and "rejected" in (await r.q.lookup(row["id"]))["message"]


def test_a_program_file_is_never_released(tmp_path):
    r = Rig(tmp_path)
    row = r.q.ingest(r.download("setup.sh", b"#!/bin/sh"), "")
    with pytest.raises(ValueError, match="program"):
        r.q.release(row["id"], tmp_path / "Downloads")


def test_discard_deletes_the_file_and_the_listing_shows_what_waits(tmp_path):
    r = Rig(tmp_path)
    a = r.q.ingest(r.download("a.pdf", b"a"), "https://x.example/")
    b = r.q.ingest(r.download("b.pdf", b"b"), "")
    assert [x["id"] for x in r.q.list()] == [a["id"], b["id"]]
    assert r.q.discard(a["id"]) and not os.path.exists(a["path"])
    assert [x["id"] for x in r.q.list()] == [b["id"]]
    assert r.q.discard(999) is False
