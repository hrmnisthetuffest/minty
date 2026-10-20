"""First-run wizard completes on a clean directory with scripted answers (W1-27)."""
import stat

from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.wizard import Wizard, needs_setup


class ScriptedIO:
    def __init__(self, answers):
        self.answers, self.said, self.asked = list(answers), [], []

    def say(self, text):
        self.said.append(text)

    def ask(self, prompt, default="", secret=False):
        self.asked.append((prompt, secret))
        value = self.answers.pop(0) if self.answers else ""
        return value or default


def make(tmp_path, answers, probe=None, mic=None):
    store = ConfigStore(tmp_path)
    store.load()
    secrets = SecretsStore(tmp_path)
    return Wizard(ScriptedIO(answers), secrets, store, probe=probe, mic_test=mic), store, secrets


async def test_it_completes_on_a_clean_directory_and_saves_what_was_given(tmp_path):
    assert needs_setup(tmp_path)

    async def probe():
        return {"groq": "ok", "gemini": "bad key"}

    answers = ["gsk-test-groq-key-not-real", "AIza-test-gemini-key", "", "", "", "",  # six provider keys
               "hi", "y", "headphones"]
    w, store, secrets = make(tmp_path, answers, probe=probe, mic=lambda: (True, "I heard you clearly."))
    r = await w.run()
    assert r.keys_saved == ["groq", "gemini"] and r.working == ["groq"] and r.mic_ok is True
    assert secrets.get("groq") == "gsk-test-groq-key-not-real"
    assert r.language == "hi" and r.listening == "headphones"
    assert store.config.stt.language == "hi" and store.config.stt.allowed_languages == ["hi"]
    assert store.config.audio.listening_setup == "headphones"
    said = " ".join(w.io.said)
    assert "Working: groq." in said and "Not working: gemini (bad key)." in said and "gsk-test" not in said
    assert all(secret for prompt, secret in w.io.asked if prompt.startswith("Key for"))
    assert stat.S_IMODE((tmp_path / "secrets.toml").stat().st_mode) == 0o600
    assert not needs_setup(tmp_path)


async def test_everything_can_be_skipped(tmp_path):
    w, store, secrets = make(tmp_path, [])
    r = await w.run()
    assert r.keys_saved == [] and set(r.skipped) >= {"keys", "check", "microphone"}
    assert secrets.names() == [] and store.config.stt.allowed_languages == ["en"]
    assert "minty-core set-key groq" in " ".join(w.io.said)


async def test_a_failing_key_check_is_reported_not_raised(tmp_path):
    async def probe():
        raise ConnectionError("no network")

    w, _, _ = make(tmp_path, ["gsk-test-key-not-real"], probe=probe)
    r = await w.run()
    assert r.keys_saved == ["groq"] and r.working == []
    assert "couldn't check them just now (ConnectionError)" in " ".join(w.io.said)


async def test_nonsense_for_the_language_falls_back_to_english(tmp_path):
    w, store, _ = make(tmp_path, ["", "", "", "", "", "", "klingon-tlh-dialect!!", "n", ""])
    r = await w.run()
    assert r.language == "en" and r.mic_ok is None


def test_the_mic_test_says_so_when_the_voice_libraries_are_missing(monkeypatch):
    import builtins
    from minty_core.wizard import mic_level_test
    real = builtins.__import__

    def blocked(name, *a, **kw):
        if name == "sounddevice":
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    ok, text = mic_level_test()
    assert ok is False and "can't test the microphone" in text
