import json

from minty_core.research.blueprint import TEMPLATES, build, detect_kind, parse_blueprint, render

SOURCES = {"S1": {"id": "S1", "title": "ESP32 guide", "url": "https://example.org/esp32"},
           "S2": {"id": "S2", "title": "Power basics", "url": "https://example.org/power"}}
CLAIMS = [{"source": "S1", "text": "The ESP32 has WiFi and runs at 3.3 volts"},
          {"source": "S2", "text": "A 2000 mAh LiPo gives about ten hours at 200 mA"}]
PLAN = {"parts": [{"item": "ESP32 board", "qty": "1", "note": "3.3 V logic", "src": ["S1", "S9"]},
                  {"item": "LiPo 2000 mAh", "qty": "1", "note": "", "src": ["S2"]}, "junk"],
        "steps": [{"step": "Wire the sensor to 3.3 V, not 5 V", "src": ["S1"]}, {"step": ""}],
        "alternatives": [{"option": "Arduino Nano", "tradeoff": "no WiFi"}],
        "risks": [{"risk": "Overcharging the LiPo", "mitigation": "Use a protected charger"}],
        "unknowns": ["Exact current draw of the sensor", ""]}


def test_kinds_are_detected_from_the_goal():
    assert detect_kind("build a weather station with an esp32 and a sensor") == "electronics"
    assert detect_kind("write a script that renames my files") == "software"
    assert detect_kind("make a wooden shelf and cut the boards") == "fabrication"
    assert detect_kind("plan my birthday") == "general" and set(TEMPLATES) >= {"electronics", "software"}


def test_parsing_removes_invented_citations_and_junk():
    bp = parse_blueprint("Here you go: " + json.dumps(PLAN), "weather station", "electronics", {"S1", "S2"})
    assert [p["item"] for p in bp.parts] == ["ESP32 board", "LiPo 2000 mAh"]
    assert bp.parts[0]["src"] == ["S1"]  # S9 does not exist, so it is gone
    assert [s["step"] for s in bp.steps] == ["Wire the sensor to 3.3 V, not 5 V"] and bp.unknowns == [
        "Exact current draw of the sensor"]
    assert parse_blueprint("no json", "g", "general", set()) is None
    assert parse_blueprint(json.dumps({"parts": [], "steps": []}), "g", "general", set()) is None


async def test_build_adds_the_second_pass_risks_without_repeating_and_renders():
    calls = []

    async def ask(system, user):
        calls.append(system[:30])
        if "careful reviewer" in system:
            return json.dumps({"risks": [{"risk": "Overcharging the LiPo", "mitigation": "again"},
                                         {"risk": "Reverse polarity kills the board", "mitigation": "Add a diode"}]})
        return json.dumps(PLAN)

    bp = await build(ask, "weather station", "electronics", CLAIMS, SOURCES)
    assert [r["risk"] for r in bp.risks] == ["Overcharging the LiPo", "Reverse polarity kills the board"]
    assert len(calls) == 2
    md = render(bp, SOURCES, "12 October 2026")
    assert md.startswith("# Blueprint: weather station") and "## Bill of materials" in md
    assert "- ESP32 board × 1: 3.3 V logic [S1]" in md and "## What could go wrong" in md
    assert "## Sources" in md and "1. Wire the sensor to 3.3 V, not 5 V [S1]" in md and "S9" not in md


async def test_without_a_model_it_is_the_evidence_and_says_so():
    async def down(system, user):
        raise RuntimeError("no model")

    for ask in (None, down):
        bp = await build(ask, "weather station", "electronics", CLAIMS, SOURCES)
        assert not bp.used_model and len(bp.steps) == 2 and "No model was available" in bp.unknowns[0]
        assert "assembled without a model" in render(bp, SOURCES, "today")
