"""Dev-loop hygiene (W0-34): parallel tests as an option, the audio stress script, no phonemizer noise."""
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_parallel_test_runner_is_an_optional_dev_dependency_listed_in_all_three_places():
    assert "pytest-xdist" in (ROOT / "pyproject.toml").read_text()
    assert "pytest-xdist" in (ROOT / "scripts" / "doctor.py").read_text()
    assert "-n auto" in (ROOT / "CONTRIBUTING.md").read_text()


def test_audio_stress_script_is_kept_and_starts():
    out = subprocess.run([sys.executable, "scripts/audio_stress.py", "--help"], capture_output=True, text=True,
                         cwd=ROOT)
    assert out.returncode == 0 and "--rounds" in out.stdout


def test_phonemizer_word_count_warnings_are_silenced_but_others_are_not(caplog):
    import minty_core.audio.tts  # noqa: F401  (installs the filter)
    from phonemizer.logger import get_logger

    logger = get_logger("normal")  # phonemizer resets its logger every time it phonemizes
    with caplog.at_level(logging.WARNING):
        logger.warning("words count mismatch on %s%% of the lines (%s/%s)", 20.0, 1, 5)
        logger.warning("something else went wrong")
    messages = [r.getMessage() for r in caplog.records]
    assert not any("words count mismatch" in m for m in messages)
    assert "something else went wrong" in messages
