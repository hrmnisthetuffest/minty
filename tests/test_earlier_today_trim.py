"""The earlier-today block of the prompt keeps its newest turns inside a budget (speed fix S-2)."""
import json
from datetime import datetime

from minty_core.config import MemoryConfig
from minty_core.history import ConversationArchive


def write_day(root, day, turns):
    (root / "raw").mkdir(parents=True, exist_ok=True)
    path = root / "raw" / f"{day}.jsonl"
    base = datetime.fromisoformat(f"{day}T09:00:00").astimezone().timestamp()
    with path.open("w") as f:
        for i in range(turns):
            f.write(json.dumps({"ts": base + i * 60, "role": "user", "text": f"question number {i} " + "x" * 90}) + "\n")
            f.write(json.dumps({"ts": base + i * 60 + 5, "role": "assistant",
                                "text": f"answer number {i} " + "y" * 200}) + "\n")


def test_earlier_today_keeps_the_recent_turns_inside_its_budget(tmp_path):
    write_day(tmp_path, "2026-10-11", 30)
    end = datetime(2026, 10, 11, 23, 0).astimezone().timestamp()
    archive = ConversationArchive(tmp_path, clock=lambda: end)
    archive.reset_at = 0
    old = archive.earlier_today(30)
    lean = archive.earlier_today(30, max_chars=2000)
    assert len(old) > 4500 and len(lean) <= 2010
    assert lean.startswith("[...]") and "answer number 29" in lean and "question number 0 " not in lean


def test_the_trim_is_a_setting_with_the_old_size_available():
    assert MemoryConfig().earlier_chars == 2000
    assert MemoryConfig(earlier_chars=5000).earlier_chars == 5000


def test_context_block_uses_the_given_budget(tmp_path):
    write_day(tmp_path, "2026-10-11", 30)
    end = datetime(2026, 10, 11, 23, 0).astimezone().timestamp()
    archive = ConversationArchive(tmp_path, clock=lambda: end)
    archive.reset_at = 0
    small = archive.context_block(30, summaries=False, earlier_chars=1500)
    full = archive.context_block(30, summaries=False)
    assert len(small) < 1500 + 400 < len(full)
