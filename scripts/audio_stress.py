#!/usr/bin/env python3
"""Barge-in stress test for the speaker: start playback, cancel it at a random moment, stop, start again, many
times. It reproduces the PortAudio close race fixed in 18756a8 (python crashed with
"PaUnixMutex_Terminate: Assertion ... failed"). Plays silence only. Run it on the laptop after audio changes:

  .venv/bin/python scripts/audio_stress.py --rounds 300
Exits 0 and prints "survived N" when nothing crashed; a hang longer than 45 s dumps the stacks and exits.
"""
import argparse
import asyncio
import faulthandler
import random
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def stress(rounds: int, device) -> None:
    import numpy as np

    from minty_core.audio.devices import SoundDevicePlayer
    from minty_core.audio.types import Audio

    store = types.SimpleNamespace(config=types.SimpleNamespace(audio=types.SimpleNamespace(output_device=device)))
    audio = Audio(pcm=np.zeros(24000 * 3 // 10, dtype=np.int16).tobytes(), rate=24000)
    player = SoundDevicePlayer(store)
    random.seed(7)
    for i in range(rounds):
        task = asyncio.create_task(player.play(audio))
        await asyncio.sleep(random.choice([0, 0.001, 0.01, 0.05, 0.12, 0.29, 0.3, 0.31]))
        task.cancel()
        player.stop()
        await asyncio.gather(task, return_exceptions=True)
        if i % 10 == 0:
            print("round", i, round(time.time() % 1000, 1), flush=True)
        if i % 3 == 0:  # the next clip straight after, as a quick barge-in does
            again = asyncio.create_task(player.play(audio))
            await asyncio.sleep(0.02)
            again.cancel()
            await asyncio.gather(again, return_exceptions=True)
    print("survived", rounds)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Speaker barge-in stress test (plays silence).")
    p.add_argument("--rounds", type=int, default=300)
    p.add_argument("--device", default=None, help="output device name or index (default: the system default)")
    args = p.parse_args(argv)
    faulthandler.dump_traceback_later(45, exit=True)
    asyncio.run(stress(args.rounds, args.device))
    return 0


if __name__ == "__main__":
    sys.exit(main())
