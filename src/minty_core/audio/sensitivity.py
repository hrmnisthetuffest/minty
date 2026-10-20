"""How loud a sound must be to count as speech, for this room and this microphone.

Microphones differ a lot (a headset mic is many times hotter than a laptop array) and so do rooms, so a fixed number is
wrong for somebody. The bar is learned from the background level instead:

- `auto_sensitivity` (on): the bar is `noise_floor_factor` times the room's level, with a small minimum (a quarter of
  `speech_rms_threshold`, never below the speech detector's own floor), so a quiet microphone in a quiet room still hears a quiet
  voice. Off: `speech_rms_threshold` is a fixed minimum, as in the first versions.
- `mic_sensitivity` scales the bar: above 1 is more sensitive (a deaf microphone), below 1 less (a hot one). The bar never goes
  lower than just above the room itself, however high the sensitivity.
- The room's level falls quickly (the music was turned down) and rises slowly (a passing noise does not become the new room).
"""
from minty_core.config import AudioConfig

AUTO_MIN_RMS = 60.0  # about -55 dBFS, the speech detector's own floor (audio.vad.QUIET_SPEECH_RMS)
AUTO_MIN_SHARE = 0.25  # of speech_rms_threshold
ROOM_MARGIN = 1.2  # the bar is at least this far above the room, whatever the sensitivity
FALL, RISE = 0.25, 0.05  # how fast the room's level follows a drop and a rise, per 80 ms frame


def track_floor(floor: float | None, level: float) -> float:
    """The room's level after one more frame of background."""
    if floor is None:
        return level
    alpha = FALL if level < floor else RISE
    return (1 - alpha) * floor + alpha * level


def speech_threshold(cfg: AudioConfig, floor: float | None) -> float:
    """The loudness (RMS of a 16-bit frame) at which a frame counts as speech."""
    base = float(cfg.speech_rms_threshold)
    factor = cfg.noise_floor_factor
    if floor is None or factor <= 0:  # the room is not known yet, or following it is switched off
        return base / cfg.mic_sensitivity
    if cfg.auto_sensitivity:
        bar = max(AUTO_MIN_RMS, base * AUTO_MIN_SHARE, floor * factor)
    else:
        bar = max(base, floor * factor)
    return max(bar / cfg.mic_sensitivity, floor * ROOM_MARGIN)
