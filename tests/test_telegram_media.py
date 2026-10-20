"""Voice codec: Ogg/Opus <-> 16 kHz PCM with PyAV (W3-11)."""
import numpy as np

from minty_core.telegram.media import CodecUnavailable, ogg_to_pcm, pcm_to_ogg


def tone(seconds=1.0, rate=22050, hz=440.0, amp=0.4):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * hz * t) * amp * 32767).astype(np.int16).tobytes()


def test_a_tone_survives_the_round_trip_with_its_length_and_loudness():
    pcm = tone()
    ogg = pcm_to_ogg(pcm, 22050)
    assert ogg[:4] == b"OggS" and b"OpusHead" in ogg[:200]  # a playable Telegram voice file
    back = ogg_to_pcm(ogg, rate=16000)
    samples = np.frombuffer(back, dtype=np.int16)
    assert abs(len(samples) / 16000 - 1.0) < 0.1
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    assert 0.5 * (0.4 * 32767 / np.sqrt(2)) < rms < 1.5 * (0.4 * 32767 / np.sqrt(2))


def test_garbage_is_an_error_not_a_crash():
    try:
        ogg_to_pcm(b"this is not audio at all")
    except CodecUnavailable as e:
        assert "audio" in str(e)
    else:
        raise AssertionError("expected CodecUnavailable")


def test_silence_and_empty_input_encode_without_error():
    assert pcm_to_ogg(b"\x00\x00" * 4000, 16000)[:4] == b"OggS"
    assert ogg_to_pcm(pcm_to_ogg(b"\x00\x00" * 4000, 16000)) != b""
