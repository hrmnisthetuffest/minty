"""Should this transcript be acted on? A cheap, pure check after speech-to-text.

Whisper is known to "hear" stock phrases in noise ("thank you", "thanks for watching", "[music]"). Those are
dropped when the audio had little real speech or the recogniser was unsure. Genuine short commands
("stop", "yes", "next song") pass.
"""
import re
from dataclasses import dataclass

HALLUCINATIONS = frozenset({
    "thank you", "thanks", "thanks for watching", "thank you for watching", "thank you so much for watching",
    "you", "please subscribe", "like and subscribe", "subtitles by the amara org community", "the end",
    "uh", "um", "hmm", "mm", "mhm", "ah", "oh", "so", "i", "the", "a",
})
_TAG = re.compile(r"[\[(][^\])]*[\])]")  # [music], (applause), [BLANK_AUDIO]
_WORD = re.compile(r"[a-z0-9']+")


@dataclass(frozen=True)
class Verdict:
    accept: bool
    reason: str = ""


def judge(text: str, *, no_speech_prob: float = 0.0, avg_logprob: float = 0.0, voiced_ms: int | None = None,
          min_voiced_ms: int = 250) -> Verdict:
    stripped = _TAG.sub(" ", text or "")
    words = _WORD.findall(stripped.lower())
    if not words:
        return Verdict(False, "sound tags only" if _TAG.search(text or "") else "no words")
    if voiced_ms is not None and voiced_ms < min_voiced_ms:
        return Verdict(False, f"only {voiced_ms} ms of speech")
    if no_speech_prob > 0.6 and avg_logprob < -1.0:
        return Verdict(False, "the recogniser was unsure it was speech")
    phrase = " ".join(words)
    unsure = (voiced_ms is not None and voiced_ms < 900) or no_speech_prob > 0.3 or avg_logprob < -0.8
    if phrase in HALLUCINATIONS and unsure:
        return Verdict(False, f"'{phrase}' is a typical mis-hearing of noise")
    if len(words) >= 6 and len(set(words)) / len(words) < 0.3:
        return Verdict(False, "looping repetition")
    return Verdict(True)
