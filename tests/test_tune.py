"""`minty tune`: say what this computer is and what suits it; change settings only when asked."""
from pathlib import Path

from minty_core.config import ConfigStore
from minty_core.hwprobe import probe
from minty_core.tune import apply, cli, system_tips
from tests.test_hwprobe import OLD_FLAGS, machine


def old_laptop(tmp_path, name="m"):
    return probe(machine(tmp_path / name, ram_kb=3_800_000, threads=2, flags=OLD_FLAGS, rotational=True), run=lambda cmd: "")


def modern(tmp_path, name="m"):
    return probe(machine(tmp_path / name, ram_kb=32_000_000, threads=16), run=lambda cmd: "")


def run(argv, tmp_path, hw, answers=()):
    out, asked = [], list(answers)
    code = cli(argv, tmp_path / "cfg", probe_fn=lambda: hw, say=out.append, ask=lambda q: asked.pop(0) if asked else "n")
    return code, "\n".join(out)


def test_by_default_it_describes_the_computer_and_changes_nothing_even_when_applied(tmp_path):
    code, text = run([], tmp_path, old_laptop(tmp_path))
    assert code == 0 and "t0" in text and "3.6 GB" in text and "looks and works the same" in text and "lite" in text
    assert "Nothing was changed" in text
    code, text = run(["--apply", "--yes"], tmp_path, old_laptop(tmp_path, "b"))
    cfg = ConfigStore(tmp_path / "cfg").load()
    assert cfg.tts.engine == "kokoro" and cfg.audio.live_captions is True and cfg.terminal_reduced_motion is False


def test_lite_shows_what_it_would_change_and_changes_nothing_until_applied(tmp_path):
    code, text = run(["--lite"], tmp_path, old_laptop(tmp_path))
    assert "tts.engine = piper" in text and "kokoro -> piper" in text and "Add --apply" in text
    assert ConfigStore(tmp_path / "cfg").load().tts.engine == "kokoro"


def test_lite_apply_with_yes_changes_the_settings_and_tells_what_they_were(tmp_path):
    code, text = run(["--lite", "--apply", "--yes"], tmp_path, old_laptop(tmp_path))
    cfg = ConfigStore(tmp_path / "cfg").load()
    assert code == 0 and cfg.tts.engine == "piper" and cfg.audio.live_captions is False
    assert cfg.terminal_reduced_motion is True and cfg.terminal_images == "off" and cfg.stt.local_model == "tiny.en"
    assert "kokoro -> piper" in text and "Changed" in text
    code, again = run(["--lite", "--apply", "--yes"], tmp_path, old_laptop(tmp_path, "again"))
    assert "already in use" in again and "Changed" not in again


def test_apply_asks_first_and_a_no_changes_nothing(tmp_path):
    code, text = run(["--lite", "--apply"], tmp_path, old_laptop(tmp_path, "a"), answers=["n"])
    assert ConfigStore(tmp_path / "cfg").load().tts.engine == "kokoro" and "Nothing was changed" in text
    code, text = run(["--lite", "--apply"], tmp_path, old_laptop(tmp_path, "b"), answers=["y"])
    assert ConfigStore(tmp_path / "cfg").load().tts.engine == "piper"


def test_full_goes_back_to_the_default_look_and_voice(tmp_path):
    run(["--lite", "--apply", "--yes"], tmp_path, old_laptop(tmp_path, "a"))
    assert ConfigStore(tmp_path / "cfg").load().tts.engine == "piper"
    code, text = run(["--full", "--apply", "--yes"], tmp_path, old_laptop(tmp_path, "b"))
    cfg = ConfigStore(tmp_path / "cfg").load()
    assert cfg.tts.engine == "kokoro" and cfg.audio.live_captions is True and cfg.terminal_reduced_motion is False
    assert cfg.terminal_images == "auto" and cfg.stt.local_model == "base.en"


def test_apply_only_touches_the_settings_in_the_profile(tmp_path):
    store = ConfigStore(tmp_path / "cfg")
    store.load()
    data = store.config.model_dump(mode="json")
    data["personality"]["humor"] = 9
    data["telegram"]["enabled"] = True
    store.update(data)
    run(["--lite", "--apply", "--yes"], tmp_path, old_laptop(tmp_path))
    cfg = ConfigStore(tmp_path / "cfg").load()
    assert cfg.personality.humor == 9 and cfg.telegram.enabled is True and cfg.tts.engine == "piper"


def test_the_system_tips_are_words_never_commands_that_run(tmp_path):
    tips = "\n".join(system_tips(old_laptop(tmp_path, "tips")))
    assert "zram" in tips and "swappiness" in tips and "spinning disk" in tips.lower()
    assert "sudo apt" not in tips  # package commands come from the OS tables, not from here
    assert not any(line.startswith(("sudo ", "rm ")) for line in tips.splitlines())
    code, text = run(["--system"], tmp_path, old_laptop(tmp_path, "sys"))
    assert "zram" in text and "Nothing was changed" in text


def test_unreadable_hardware_still_gives_an_answer(tmp_path):
    code, text = run([], tmp_path, probe(tmp_path / "nothing", run=lambda cmd: ""))
    assert code == 0 and "t1" in text
