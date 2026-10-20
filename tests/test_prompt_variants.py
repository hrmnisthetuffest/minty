"""Compressed prompt variant behind an evaluation gate (W0-28)."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from minty_core import prompts
from minty_core.frugal import frugal_gate
from minty_core.personality import BASE_RULES

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ORIGINALS = {"BASE_RULES": BASE_RULES, "ANSWER_RULES": prompts.ANSWER_RULES, "EVOLVE_RULES": prompts.EVOLVE_RULES}


def test_terse_variants_are_at_least_25_percent_smaller_in_total():
    compress = load("compress_prompts")
    terse = compress.terse_sections(ORIGINALS)
    before = sum(len(v) for v in ORIGINALS.values())
    after = sum(len(v) for v in terse.values())
    assert after <= 0.75 * before, (before, after)


@pytest.mark.parametrize("name", list(ORIGINALS))
def test_every_tool_name_and_safety_sentence_survives(name):
    compress = load("compress_prompts")
    terse = compress.terse_sections(ORIGINALS)[name]
    for tool in re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", ORIGINALS[name]):
        assert tool in terse, tool
    for sentence in compress.safety_sentences(ORIGINALS[name]):
        assert sentence in terse, sentence


def test_the_committed_variant_matches_the_script():
    compress = load("compress_prompts")
    data = json.loads((ROOT / "src" / "minty_core" / "prompts_terse.json").read_text())
    assert data["sections"] == compress.terse_sections(ORIGINALS)
    assert data["gate"]["passed"] is False  # not evaluated with a live A/B yet


def test_the_gate_needs_enough_trials_fewer_tokens_and_no_real_loss():
    f0 = [{"task": f"t{i}", "passed": True, "tokens_est": 1000} for i in range(10)]
    good = [{"task": f"t{i}", "passed": i != 0, "tokens_est": 700} for i in range(10)]
    assert frugal_gate(f0 * 2, good * 2)["passed"] is False  # 10 points lost
    better = [{"task": f"t{i}", "passed": True, "tokens_est": 700} for i in range(10)]
    gate = frugal_gate(f0, better)
    assert gate["passed"] and gate["token_saving"] == 0.3 and gate["success_loss_points"] == 0.0
    assert frugal_gate(f0, [dict(r, tokens_est=900) for r in better])["passed"] is False  # only 10 % fewer
    assert frugal_gate(f0[:5], better[:5])["passed"] is False  # too few trials
    infra = [dict(r, infra=True) for r in better]
    assert frugal_gate(f0, infra)["passed"] is False


def test_prompt_sections_follow_the_level_and_the_gate(monkeypatch):
    assert prompts.section("ANSWER_RULES", terse=False) == prompts.ANSWER_RULES
    assert prompts.section("ANSWER_RULES", terse=True) == prompts.ANSWER_RULES  # gate not passed
    monkeypatch.setattr(prompts, "_TERSE", {"sections": {"ANSWER_RULES": "short"}, "gate": {"passed": True}})
    assert prompts.section("ANSWER_RULES", terse=True) == "short"
    assert prompts.section("ANSWER_RULES", terse=False) == prompts.ANSWER_RULES


def test_gate_script_writes_the_result(tmp_path):
    f0, f1 = tmp_path / "f0.jsonl", tmp_path / "f1.jsonl"
    f0.write_text("\n".join(json.dumps({"task": f"t{i}", "passed": True, "tokens_est": 1000}) for i in range(10)))
    f1.write_text("\n".join(json.dumps({"task": f"t{i}", "passed": True, "tokens_est": 600}) for i in range(10)))
    target = tmp_path / "terse.json"
    target.write_text(json.dumps({"sections": {}, "gate": {"passed": False}}))
    out = subprocess.run([sys.executable, "scripts/frugal_gate.py", str(f0), str(f1), "--target", str(target)],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    gate = json.loads(target.read_text())["gate"]
    assert gate["passed"] and gate["token_saving"] == 0.4
