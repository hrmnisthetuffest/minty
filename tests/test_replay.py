"""Replay a recorded day against the current build (W0-32)."""
import json
import subprocess
import sys
from pathlib import Path

from minty_core.replay import load_day, replay_fake, summarise_replay

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-10-11"


def write_day(config: Path):
    raw = config / "history" / "raw"
    raw.mkdir(parents=True)
    rows = [
        {"ts": 1.0, "role": "user", "text": "Could you open Chrome?", "private": False, "turn_id": "a1"},
        {"ts": 2.0, "role": "assistant", "text": "Chrome is open.", "private": False, "turn_id": "a1"},
        {"ts": 3.0, "role": "user", "text": "my diary entry for today", "private": True, "turn_id": "a2"},
        {"ts": 4.0, "role": "user", "text": "email alex@example.com the plan", "private": False, "turn_id": "a3"},
        {"ts": 5.0, "role": "user", "text": "what time is it", "private": False},
    ]
    (raw / f"{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    traces = config / "traces"
    traces.mkdir()
    events = [{"ts": 1.5, "kind": "turn", "turn_id": "a1", "outcome": "barged", "first_audio_ms": 9100.0},
              {"ts": 4.5, "kind": "turn", "turn_id": "a3", "outcome": "answered", "first_audio_ms": 6200.0}]
    (traces / f"{DAY}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_load_day_skips_private_turns_and_joins_the_original_outcome(tmp_path):
    write_day(tmp_path)
    turns = load_day(tmp_path, DAY)
    assert [t["text"] for t in turns] == ["Could you open Chrome?", "email alex@example.com the plan",
                                          "what time is it"]
    assert turns[0]["before"] == {"outcome": "barged", "first_audio_ms": 9100.0}
    assert turns[2]["before"] is None
    assert len(load_day(tmp_path, DAY, include_private=True)) == 4


def test_scrubbing_hides_contact_details_and_names(tmp_path):
    write_day(tmp_path)
    turns = load_day(tmp_path, DAY, scrub=True, names=["Alex"])
    assert "alex" not in json.dumps(turns).lower() and "EMAIL_1" in turns[1]["text"]


async def test_fake_replay_measures_every_request_without_spending_quota(tmp_path):
    write_day(tmp_path)
    rows = await replay_fake(load_day(tmp_path, DAY), work_dir=tmp_path / "work")
    assert len(rows) == 3
    for row in rows:
        assert row["reply"] and row["first_sentence_ms"] is not None
        if row["instant"]:
            assert row["llm_calls"] == 0 and row["chars_sent"] == 0  # answered by the computer itself, no quota
        else:
            assert row["llm_calls"] >= 1 and row["chars_sent"] > 0 and row["tools_offered"] > 0
    assert [row["instant"] for row in rows] == [False, False, True]  # "what time is it" needs no model any more
    assert rows[0]["before"]["outcome"] == "barged"
    summary = summarise_replay(rows)
    assert summary["requests"] == 3 and summary["answered"] == 3
    assert summary["before_first_audio_p50_ms"] in (6200.0, 9100.0) and summary["chars_sent_median"] > 0


def test_cli_writes_a_report(tmp_path):
    write_day(tmp_path)
    out = tmp_path / "report.json"
    run = subprocess.run([sys.executable, "scripts/replay_day.py", "--day", DAY, "--config", str(tmp_path),
                          "--fake", "--scrub", "--out", str(out)], capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stderr
    report = json.loads(out.read_text())
    assert report["summary"]["requests"] == 3 and "alex" not in out.read_text().lower()
    assert "requests" in run.stdout


def test_cli_refuses_real_providers_without_an_explicit_yes(tmp_path):
    write_day(tmp_path)
    run = subprocess.run([sys.executable, "scripts/replay_day.py", "--day", DAY, "--config", str(tmp_path)],
                         capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 2 and "--fake" in run.stderr


async def test_real_mode_copies_config_and_keys_into_a_private_scratch_folder(tmp_path, monkeypatch):
    from minty_core.config import ConfigStore
    from minty_core.keystore import SecretsStore
    from minty_core import replay

    write_day(tmp_path)
    ConfigStore(tmp_path).load()
    SecretsStore(tmp_path).set("groq", "gsk_abcdefghijklmnop")
    seen = {}

    async def fake_run(ctx, turns):
        seen["key"] = ctx.secrets.get("groq")
        seen["ledger"] = str(ctx.router.ledger._db.execute("PRAGMA database_list").fetchone()[2])
        return []

    monkeypatch.setattr(replay, "_run", fake_run)
    work = tmp_path / "scratch"
    await replay.replay_real([], tmp_path, work)
    assert seen["key"] == "gsk_abcdefghijklmnop" and seen["ledger"] == str(tmp_path / "capacity.sqlite")
    assert work.stat().st_mode & 0o777 == 0o700
