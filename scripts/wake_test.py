"""Live wake-word meter: say "hey jarvis" and watch the score. Ctrl+C to stop."""
import numpy as np
import sounddevice as sd

from minty_core.audio.devices import _load_oww
from minty_core.config import ConfigStore
from minty_core.paths import config_dir

cfg = ConfigStore(config_dir()).load().audio
model = _load_oww(cfg.wake_word)
best, frames = 0.0, 0
print(f"wake word '{cfg.wake_word}', threshold {cfg.wake_threshold}. Say it now (Ctrl+C to stop)")
with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280) as s:
    while True:
        data, _ = s.read(1280)
        pcm = data[:, 0]
        score = max(model.predict(pcm).values(), default=0.0)
        best = max(best, score)
        frames += 1
        if frames % 6 == 0 or score > 0.2:
            rms = float(np.sqrt((pcm.astype(np.float32) ** 2).mean()))
            bar = "#" * int(score * 30)
            hit = "  <-- WOKE" if score >= cfg.wake_threshold else ""
            print(f"rms {rms:5.0f}  score {score:4.2f} {bar:<30} best {best:4.2f}{hit}")
