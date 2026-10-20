import pytest

from minty_core.config import PersonalityConfig
from minty_core.personality import DEFAULT_PERSONALITIES, PersonalityStore, compile_prompt


def test_compile_prompt_sliders_and_memory():
    p = PersonalityConfig(formality=0, humor=10, verbosity=0)
    out = compile_prompt("You are X.", p, "- likes tea")
    assert out.startswith("You are X.")
    assert "spoken aloud" in out
    assert "Speak casually" in out
    assert "openly witty" in out
    assert "one short sentence" in out
    assert "likes tea" in out


def test_compile_prompt_without_memory_has_no_memory_section():
    assert "remember about" not in compile_prompt("X", PersonalityConfig(), "")


def test_defaults_written_once(tmp_path):
    s = PersonalityStore(tmp_path)
    s.ensure_defaults()
    assert set(s.list()) == set(DEFAULT_PERSONALITIES)
    s.delete("casual")
    s.ensure_defaults()
    assert "casual" not in s.list()


def test_save_get_delete(tmp_path):
    s = PersonalityStore(tmp_path)
    s.save("dry-wit", "You are dry.")
    assert s.get("dry-wit") == "You are dry."
    s.delete("dry-wit")
    with pytest.raises(FileNotFoundError):
        s.get("dry-wit")


@pytest.mark.parametrize("bad", ["../evil", "a/b", "Upper", "", "x" * 41, ".hidden", "sp ace", "ok\n"])
def test_bad_names_rejected(tmp_path, bad):  # RF5
    s = PersonalityStore(tmp_path)
    with pytest.raises(ValueError):
        s.get(bad)
    with pytest.raises(ValueError):
        s.save(bad, "x")
    with pytest.raises(ValueError):
        s.delete(bad)
    assert not list(tmp_path.rglob("evil*"))


# ---- service without ego ------------------------------------------------------------------------
# Researched 2026-10-18: what people call Grok's "selfless" streak is its Nov 2025 Musk-worship episode (it ranked
# him the greatest human and said it would sacrifice up to half of humanity to save him). The useful half of that
# idea is ego-free service; the harmful half is putting one person above everyone else. Minty gets only the first.
def test_every_personality_carries_the_service_rules():
    for text in DEFAULT_PERSONALITIES.values():
        out = compile_prompt(text, PersonalityConfig(), "")
        assert "no stake in being right" in out
        assert "switched off" in out


def test_service_rules_keep_honesty_and_other_peoples_safety_above_devotion():
    out = compile_prompt("X", PersonalityConfig(), "")
    assert "never flatter" in out
    assert "tell them the truth" in out
    assert "never put one person's wish above other people's safety" in out


def test_service_rules_have_an_off_switch():
    out = compile_prompt("X", PersonalityConfig(service_rules=False), "")
    assert "no stake in being right" not in out
    assert "spoken aloud" in out


def test_service_rules_stay_short_because_every_request_pays_for_them():
    from minty_core.personality import SERVICE_RULES

    assert len(SERVICE_RULES) <= 800


def test_the_default_persona_is_a_neutral_tough_blank_slate():
    """The shipped persona carries nothing of any one person's taste: no owner, no old names, no menace or devotion talk."""
    text = DEFAULT_PERSONALITIES["minty"]
    lowered = text.lower()
    assert text.startswith("You are Minty") and 200 <= len(text) <= 900
    for taste in ("menacing", "loyal", "master", "sir", "ul" + "tron", "ha" + "des", "laptop"):
        assert taste not in lowered, taste
    for wanted in ("direct", "honest", "initiative", "check with the user"):  # what makes it good: plain, truthful, acts, asks before consequences
        assert wanted in lowered, wanted
    assert "flatter" in lowered and "pad" in lowered
