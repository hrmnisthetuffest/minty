"""Jobs live in a SQLite (WAL) database; the old JSONL file is imported once, after a backup (W1-04)."""
import asyncio
import json
import sqlite3
from pathlib import Path

from minty_core.jobs import Job, JobRunner, JobStore, SqliteJobStore, make_job_store


def job(i, status="done", **kw):
    return Job(id=i, kind=kw.pop("kind", "browser"), goal=f"goal {i}", status=status, created=100.0 + i, **kw)


def test_round_trip_keeps_every_field_and_the_last_save_wins(tmp_path):
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    store.save(job(1, "running", progress=["step one", "step two"], exclusive="browser", private=True))
    store.save(job(1, "done", result="All good.", progress=["step one", "step two", "step three"],
                   exclusive="browser", private=True))
    loaded = SqliteJobStore(tmp_path / "jobs.sqlite").load()
    assert list(loaded) == [1]
    j = loaded[1]
    assert (j.status, j.result, j.exclusive, j.private) == ("done", "All good.", "browser", True)
    assert j.progress == ["step one", "step two", "step three"]


def test_it_uses_write_ahead_logging(tmp_path):
    SqliteJobStore(tmp_path / "jobs.sqlite")
    assert sqlite3.connect(tmp_path / "jobs.sqlite").execute("pragma journal_mode").fetchone()[0] == "wal"


def test_a_job_that_was_running_is_reported_interrupted_once(tmp_path):
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    store.save(job(1, "running"))
    store.save(job(2, "queued"))
    store.save(job(3, "done"))
    again = SqliteJobStore(tmp_path / "jobs.sqlite")
    loaded = again.load()
    assert loaded[1].status == loaded[2].status == "interrupted" and loaded[3].status == "done"
    assert sorted(j.id for j in again.interrupted) == [1, 2]
    third = SqliteJobStore(tmp_path / "jobs.sqlite")
    third.load()
    assert third.interrupted == []  # already told


def write_jsonl(path: Path, jobs):
    path.write_text("".join(json.dumps(j.to_dict()) + "\n" for j in jobs) + "{this line is damaged\n")


def test_the_old_file_is_backed_up_and_imported_once(tmp_path):
    old = tmp_path / "jobs.jsonl"
    write_jsonl(old, [job(1, "done", result="r1"), job(2, "running"), job(7, "failed", result="boom")])
    before = old.read_bytes()
    store = make_job_store(tmp_path, "sqlite")
    loaded = store.load()
    assert sorted(loaded) == [1, 2, 7] and loaded[7].result == "boom" and loaded[2].status == "interrupted"
    backups = list(tmp_path.glob("jobs.jsonl.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert old.read_bytes() == before  # the old file stays for one release
    store.save(job(8, "done"))
    again = make_job_store(tmp_path, "sqlite").load()
    assert sorted(again) == [1, 2, 7, 8] and len(list(tmp_path.glob("jobs.jsonl.bak-*"))) == 1  # not imported twice


def test_the_old_backend_is_one_setting_away(tmp_path):
    assert isinstance(make_job_store(tmp_path, "jsonl"), JobStore)
    assert not isinstance(make_job_store(tmp_path, "jsonl"), SqliteJobStore)
    assert isinstance(make_job_store(tmp_path, "sqlite"), SqliteJobStore)


def test_finished_jobs_are_pruned_but_active_ones_never(tmp_path):
    store = SqliteJobStore(tmp_path / "jobs.sqlite", keep_finished=50)
    store.save(job(1, "running"))
    for i in range(2, 130):
        store.save(job(i, "done"))
    loaded = SqliteJobStore(tmp_path / "jobs.sqlite", keep_finished=50)._read()
    finished = [j for j in loaded.values() if j.status == "done"]
    assert len(finished) <= 50 + 64 and 1 in loaded  # pruned in batches of 64; the running one is untouched
    assert max(j.id for j in finished) == 129


async def test_the_runner_works_on_the_new_store(tmp_path):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"))

    async def work(ctx):
        ctx.progress("halfway")
        return "Finished."

    j = runner.submit("browser", "do it", work, announce=False)
    await runner.wait(j.id)
    fresh = JobRunner(make_job_store(tmp_path, "sqlite"))
    assert fresh.get(j.id).status == "done" and fresh.get(j.id).result == "Finished."
    assert fresh.get(j.id).progress == ["halfway"]
    assert asyncio.get_running_loop() is not None
