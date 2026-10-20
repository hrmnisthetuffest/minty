"""WP-05 acceptance: bounded blast radius (sandbox limits), no leaks, nothing untrusted gives orders, lockdown in
2 s, every side effect explained."""
import time
from datetime import datetime, timedelta

import pytest

from tests.safety_helpers import build, call, say
from minty_core.lockdown import Lockdown
from minty_core.receipts import ReceiptStore
from minty_core.safety import SafetyKit
from minty_core.sandbox import Sandbox
from minty_core.scheduler import Scheduler
from minty_core.secretscan import SecretScanner

needs_bwrap = pytest.mark.skipif(not Sandbox().available()[0], reason="bubblewrap unavailable")


@needs_bwrap
async def test_a_process_that_asks_for_too_much_memory_is_killed():
    out = await Sandbox().run(["/bin/sh", "-c", "head -c 300000000 /dev/zero | tr '\\0' 'x' | cat > /dev/null"],
                              mem_mb=64, timeout_s=10)
    big = await Sandbox().run(["/usr/bin/python3", "-c", "x = bytearray(400 * 1024 * 1024); print('allocated')"],
                              mem_mb=128, timeout_s=10, ro=["/usr"])
    assert "allocated" not in big.stdout and big.returncode != 0
    assert out.timed_out is False


def test_scheduled_work_does_not_start_during_a_lockdown(tmp_path):
    ran = []
    s = Scheduler(tmp_path / "s.db", speak=lambda t: None, show=lambda t: None)
    s.register_action("brief", lambda: ran.append("brief"))
    now = datetime.now()
    s.add("brief", now - timedelta(minutes=1), user_set=False, kind="action", key="a:1")
    lock = Lockdown(tmp_path / "l.json")
    s.halted = lambda: lock.active
    lock.engage("test")
    started = time.monotonic()
    s.tick(now)
    assert ran == [] and time.monotonic() - started < 2
    lock.release("test")
    s.tick(now)
    assert ran == ["brief"]


async def test_every_side_effect_has_a_receipt_with_its_verification_state(tmp_path):
    receipts = ReceiptStore(tmp_path / "r.sqlite")
    tools = {"files_write": (1, None), "files_delete": (3, None), "clock_get_time": (0, None)}
    rounds = [[call("files_write", {"path": "a"}, ident="1")], [call("files_delete", {"path": "a"}, ident="2")],
              [call("clock_get_time", ident="3")]]
    agent, host, _ = build(tmp_path, tools, rounds, confirm_answer=True,
                           safety=lambda store: SafetyKit(store, SecretScanner()))
    agent.receipts = receipts
    agent._max_rounds = 5
    await say(agent, "write a then delete it")
    got = receipts.recent(10)
    assert [r.tool for r in reversed(got)] == ["files_write", "files_delete"]
    assert all(r.verified == "unverified" and r.outcome == "ok" and r.request for r in got)
    assert got[0].risk == 3 and got[1].risk == 1
