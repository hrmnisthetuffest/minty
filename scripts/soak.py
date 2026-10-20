#!/usr/bin/env python3
"""Soak: drive Minty's bookkeeping with synthetic use for a long time and report any growth trend.

  scripts/soak.py --minutes 30          a quick one
  scripts/soak.py --hours 72            the real one, on the laptop, on a spare profile

It builds a context in a scratch folder (never your real one), then repeats: jobs, approvals, receipts, journal
saves, idempotency, config changes, traces. Memory, descriptors, threads and WAL size are sampled; it exits 1 and
says what grew if the leak guard alarms. Nothing here talks to a provider or uses a microphone.
"""
import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minty_core import leakguard  # noqa: E402
from minty_core.context import build_context  # noqa: E402
from minty_core.llm.types import Message  # noqa: E402


async def cycle(ctx, i: int) -> None:
    async def work(job_ctx):
        job_ctx.progress(f"step {i}")
        return "done"

    job = ctx.services["jobs"].submit("research", f"soak {i}", work, announce=False)
    await ctx.services["jobs"].wait(job.id)
    ap = ctx.services["approvals"].request("mail_send", {"n": i}, "Send?", risk=3)
    ctx.services["approvals"].decide(ap.id, i % 2 == 0)
    ctx.services["receipts"].record("files_write", 1, f"write {i}", request="soak", outcome="ok")
    ctx.services["journal"].save([Message("user", f"hello {i}"), Message("assistant", "hi")])
    ctx.services["tracer"].event("soak", i=i)
    if i % 50 == 0:
        data = ctx.store.config.model_dump(mode="json")
        data["turn"]["keep_full_turns"] = 4 + (i // 50) % 3
        ctx.store.update(data)


async def main(minutes: float, every_s: float) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        ctx = build_context(Path(tmp))
        guard = leakguard.LeakGuard()
        end, last, i = time.time() + minutes * 60, 0.0, 0
        while time.time() < end:
            await cycle(ctx, i)
            i += 1
            if time.time() - last >= every_s:
                guard.add(leakguard.take(Path(tmp)))
                last = time.time()
            await asyncio.sleep(0)
        guard.add(leakguard.take(Path(tmp)))
        first, final = guard.samples[0], guard.samples[-1]
        print(f"{i} cycles; memory {first.rss_mb:.0f} -> {final.rss_mb:.0f} MB, files {first.fds} -> {final.fds}, "
              f"threads {first.threads} -> {final.threads}, WAL {final.wal_mb:.1f} MB")
        problems = guard.alarms()
        for p in problems:
            print("ALARM:", p)
        return 1 if problems else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--sample-every", type=float, default=30.0, help="seconds between samples")
    args = ap.parse_args()
    total = args.minutes + args.hours * 60 or 1.0
    raise SystemExit(asyncio.run(main(total, args.sample_every)))
