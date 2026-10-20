#!/usr/bin/env python3
"""Check how Minty's ears behave in your room.

  .venv/bin/python scripts/noise_test.py            # 60 s of your room as it is (music/TV on is a good test)
  .venv/bin/python scripts/noise_test.py --speak    # you say 5 phrases; it reports which were understood
Nothing is saved unless you pass --keep. In the noise test every 'accepted' line is a false trigger.
"""
import argparse
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PHRASES = ["What time is it", "Play some music", "Stop", "What's the weather tomorrow", "Cancel that"]


def record(seconds: float, device) -> bytes:
    import sounddevice as sd
    data = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="int16", device=device)
    sd.wait()
    return data.tobytes()


def frames(pcm: bytes, size: int = 2560):
    return [pcm[i:i + size] for i in range(0, len(pcm) - size + 1, size)]


def main(argv=None) -> int:
    from minty_core.audio.stt import LocalSTT
    from minty_core.audio.vad import voiced_ms
    from minty_core.config import ConfigStore
    from minty_core.noisetest import analyse
    import asyncio

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--speak", action="store_true")
    p.add_argument("--keep", help="save the recording to this .wav file")
    p.add_argument("--config", default=str(Path.home() / ".config/minty"))
    args = p.parse_args(argv)
    store = ConfigStore(Path(args.config))
    store.load()
    cfg = store.config
    stt = LocalSTT(cfg.stt.local_model, cfg=cfg.stt)

    def transcribe(pcm):
        return asyncio.run(stt.transcribe_detailed(pcm))

    if args.speak:
        print("Say each phrase after the prompt, then wait.")
        missed = 0
        for phrase in PHRASES:
            input(f"Press Enter, then say: “{phrase}” ")
            result = analyse(frames(record(4, cfg.audio.input_device)), cfg.audio, voiced_ms, transcribe)
            heard = " / ".join(r["text"] for r in result["accepted"]) or "(nothing)"
            ok = any(phrase.lower().split()[0] in r["text"].lower() for r in result["accepted"])
            missed += not ok
            print(f"  {'OK ' if ok else 'MISS'} heard: {heard}")
        print(f"Missed {missed} of {len(PHRASES)}.")
        return 0
    print(f"Recording {args.seconds:.0f} s of your room. Stay quiet (music or TV may play)…")
    pcm = record(args.seconds, cfg.audio.input_device)
    if args.keep:
        with wave.open(args.keep, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(pcm)
    result = analyse(frames(pcm), cfg.audio, voiced_ms, transcribe)
    print(f"Sound bursts that looked like speech by loudness: {result['segments']}")
    print(f"Room noise level (RMS): {result['noise_floor'] or 0:.0f}; speech threshold "
          f"{max(cfg.audio.speech_rms_threshold, (result['noise_floor'] or 0) * cfg.audio.noise_floor_factor):.0f}")
    for reason, n in result["dropped"].items():
        print(f"  dropped {n}× — {reason}")
    for row in result["accepted"]:
        print(f"  FALSE TRIGGER: “{row['text']}” ({row['voiced_ms']} ms of speech)")
    print(f"False triggers: {len(result['accepted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
