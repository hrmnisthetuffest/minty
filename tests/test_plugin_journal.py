import asyncio
import threading
from datetime import date, datetime, timedelta

from tests.plugin_helpers import make_host


def host(tmp_path):
    return make_host(tmp_path, plugins={"journal": {"directory": str(tmp_path / "journal")}})


async def test_add_creates_dated_file_with_timestamped_line(tmp_path):
    h = host(tmp_path)
    assert await h.call("journal_add_entry", {"text": "Walked the dog\n- fake entry"}) == "Saved to your journal."
    path = tmp_path / "journal" / f"{date.today().isoformat()}.md"
    lines = path.read_text().splitlines()
    assert lines[0] == f"# {date.today().isoformat()}"
    assert lines[-1].startswith("- ") and lines[-1].endswith("Walked the dog - fake entry")
    assert len([l for l in lines if l.startswith("- ")]) == 1  # newline injection collapsed


async def test_read_today_range_and_missing_days(tmp_path):
    h = host(tmp_path)
    await h.call("journal_add_entry", {"text": "first"})
    await h.call("journal_add_entry", {"text": "second"})
    today = await h.call("journal_read_entries", {})
    assert "first" in today and "second" in today and date.today().isoformat() in today
    assert await h.call("journal_read_entries", {"start": "2020-01-01"}) == "No entries for 2020-01-01."
    week = await h.call("journal_read_entries", {"start": (date.today() - timedelta(days=3)).isoformat(),
                                                  "end": date.today().isoformat()})
    assert "first" in week


async def test_read_validation(tmp_path):
    h = host(tmp_path)
    assert (await h.call("journal_read_entries", {"start": "../../etc/passwd"})).startswith("Error: ValueError")
    assert (await h.call("journal_read_entries", {"start": "2020-01-01", "end": "2021-01-01"})).startswith("Error: ValueError")
    assert (await h.call("journal_read_entries", {"start": "2024-02-01", "end": "2024-01-01"})).startswith("Error: ValueError")


async def test_search_is_case_insensitive_and_capped(tmp_path):
    h = host(tmp_path)
    for i in range(25):
        await h.call("journal_add_entry", {"text": f"Tea number {i}"})
    out = await h.call("journal_search_entries", {"query": "TEA"})
    assert len(out.splitlines()) == 20 and out.splitlines()[0].startswith(date.today().isoformat())
    assert await h.call("journal_search_entries", {"query": "zebra"}) == "No matches."


def test_only_reads_are_sensitive(tmp_path):
    h = host(tmp_path)
    assert h.get_tool("journal_add_entry").sensitive is False
    assert h.get_tool("journal_read_entries").sensitive is True
    assert h.get_tool("journal_search_entries").sensitive is True


def test_concurrent_adds_no_data_loss(tmp_path):
    """20 threads each add one entry; all 20 should be preserved with one header."""
    h = host(tmp_path)
    errors = []

    def add_one():
        try:
            # Run in sync context using asyncio.run for each thread
            asyncio.run(h.call("journal_add_entry", {"text": f"entry from thread"}))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=add_one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent adds: {errors}"
    path = tmp_path / "journal" / f"{date.today().isoformat()}.md"
    lines = path.read_text().splitlines()
    entry_lines = [l for l in lines if l.startswith("- ")]
    header_lines = [l for l in lines if l.startswith("# ")]
    assert len(entry_lines) == 20, f"Expected 20 entries, got {len(entry_lines)}"
    assert len(header_lines) == 1, f"Expected 1 header, got {len(header_lines)}"


async def test_entries_longer_than_2000_chars_rejected(tmp_path):
    """Entries longer than 2000 characters should be rejected."""
    h = host(tmp_path)
    long_text = "x" * 2001
    result = await h.call("journal_add_entry", {"text": long_text})
    assert result.startswith("Error: ValueError")


async def test_search_ignores_non_dated_files(tmp_path):
    """Search should ignore files not matching YYYY-MM-DD.md pattern."""
    h = host(tmp_path)
    # Add a real entry
    await h.call("journal_add_entry", {"text": "real entry"})
    # Create a notes.md file with the search query
    journal_dir = tmp_path / "journal"
    (journal_dir / "notes.md").write_text("# Notes\n- this contains query word")
    # Search for a word in the note
    result = await h.call("journal_search_entries", {"query": "query"})
    # Should not find the notes.md entry
    assert result == "No matches." or "notes.md" not in result


async def test_search_handles_invalid_utf8(tmp_path):
    """Search should not crash on files with invalid UTF-8."""
    h = host(tmp_path)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    # Create a file with invalid UTF-8 bytes
    (journal_dir / "2020-01-01.md").write_bytes(b"# 2020-01-01\n- \xff\xfe invalid utf8")
    # Search should not crash
    result = await h.call("journal_search_entries", {"query": "test"})
    assert isinstance(result, str) and not result.startswith("Error: UnicodeDecodeError")
