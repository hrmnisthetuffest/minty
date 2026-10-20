"""604: meaning-based search is adopted only if it measurably improves recall on your own questions."""
import json

import pytest

from minty_core.config import Config
from minty_core.vault.gate import (GateResult, HashEmbedder, evaluate, hybrid_ranker, load_result, recall_at_k, rrf,
                                   save_result)
from minty_core.vault.kit import VaultProvider
from minty_core.vault.store import Vault


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "brain", git=False)
    for title, body in [
        ("Camera wiring", "The ribbon cable goes in first, blue side up, before the lens mount."),
        ("Servo torque", "Stall torque of the MG996R is about ten kilogram centimetres at six volts."),
        ("School timetable", "Maths on Monday first period, physics Thursday, chemistry lab on Friday."),
        ("Protein plan", "Aim for about one hundred and twenty grams of protein every day."),
        ("Pocket money", "Savings goal is a new keyboard by December."),
    ]:
        v.create(title, body, folder="notes")
    return v


def ids(vault):
    return {n.title: n.id for n in vault.list("notes")}


def test_recall_at_k_counts_questions_whose_note_is_in_the_top_k():
    ranker = {"a": ["x", "y"], "b": ["y", "x"], "c": ["z"]}.get
    qs = [("a", "x"), ("b", "x"), ("c", "x")]
    assert recall_at_k(lambda q: ranker(q), qs, k=1) == pytest.approx(1 / 3)
    assert recall_at_k(lambda q: ranker(q), qs, k=2) == pytest.approx(2 / 3)


def test_reciprocal_rank_fusion_rewards_agreement():
    assert rrf(["a", "b", "c"], ["c", "a", "d"])[:2] == ["a", "c"]


def test_the_hash_embedder_makes_similar_words_close_and_is_stable():
    e = HashEmbedder()
    same = e.cosine(e.vector("camera wiring"), e.vector("camera wiring"))
    typo = e.cosine(e.vector("camera wiring"), e.vector("camra wirring"))
    other = e.cosine(e.vector("camera wiring"), e.vector("school timetable"))
    assert same == pytest.approx(1.0) and typo > 0.3 and other < 0.1
    assert e.vector("camera") == HashEmbedder().vector("camera")


def test_a_typo_heavy_question_set_passes_the_gate(vault):
    i = ids(vault)
    questions = [("camra ribon cabel order", i["Camera wiring"]), ("servoo torqe stal", i["Servo torque"]),
                 ("scool timetabel fisics", i["School timetable"]), ("protien plan dayly", i["Protein plan"]),
                 ("pockit mony savngs keybord", i["Pocket money"])]
    r = evaluate(vault, questions, min_questions=1)
    assert r.baseline_recall < r.candidate_recall and r.gain_points >= 10 and r.passed
    assert r.questions == 5 and r.candidate_ms >= 0


def test_when_plain_search_is_already_perfect_the_gate_fails_and_says_why(vault):
    i = ids(vault)
    questions = [("ribbon cable lens", i["Camera wiring"]), ("stall torque volts", i["Servo torque"]),
                 ("timetable physics", i["School timetable"]), ("protein grams", i["Protein plan"])]
    r = evaluate(vault, questions, min_questions=1)
    assert r.baseline_recall == 1.0 and not r.passed and "gain" in r.reason


def test_too_few_questions_cannot_pass(vault):
    i = ids(vault)
    r = evaluate(vault, [("camra", i["Camera wiring"])])
    assert not r.passed and "100 questions" in r.reason


def test_a_slow_candidate_fails_the_latency_limit(vault):
    i = ids(vault)
    questions = [("camra ribon cabel order", i["Camera wiring"])] * 3
    r = evaluate(vault, questions, min_questions=1, max_ms=0.0)
    assert not r.passed and "ms" in r.reason


def test_the_result_is_saved_and_loaded(tmp_path):
    r = GateResult(0.4, 0.6, 20.0, 3.2, 100, True, "recall improved by 20.0 points", "2026-10-15T10:00:00", "laptop")
    p = tmp_path / "gate.json"
    save_result(p, r)
    assert load_result(p) == r
    assert load_result(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert load_result(tmp_path / "bad.json") is None


def test_hybrid_search_stays_off_without_a_passing_gate_even_when_switched_on(tmp_path, vault):
    cfg = Config()
    cfg.vault.enabled, cfg.vault.path, cfg.vault.git, cfg.vault.hybrid = True, str(vault.root), False, True
    kit = VaultProvider(config=lambda: cfg).kit()
    assert kit.hybrid_state().startswith("off") and "not been run" in kit.hybrid_state()
    assert kit.search.ranker is None
    save_result(vault.root / "_minty" / "gate.json",
                GateResult(0.2, 0.5, 30.0, 4.0, 100, True, "ok", "2026-10-15T10:00:00", "laptop"))
    kit = VaultProvider(config=lambda: cfg).kit()
    assert kit.hybrid_state() == "on" and kit.search.ranker is not None
    cfg.vault.hybrid = False
    assert VaultProvider(config=lambda: cfg).kit().search.ranker is None


def test_the_hybrid_ranker_finds_a_typo_question_that_plain_search_misses(vault):
    i = ids(vault)
    plain = vault.search_ids("camra ribon cabel", limit=5)
    assert i["Camera wiring"] not in plain
    assert hybrid_ranker(vault, HashEmbedder())("camra ribon cabel", 5)[0] == i["Camera wiring"]


def test_questions_file_round_trip_for_the_cli(tmp_path, vault):
    from minty_core.vault.gate import load_questions

    i = ids(vault)
    p = tmp_path / "q.json"
    p.write_text(json.dumps([{"q": "camra ribon", "note": i["Camera wiring"]},
                             {"q": "servoo torqe", "note": "Servo torque"}]))
    qs = load_questions(p, vault)
    assert qs == [("camra ribon", i["Camera wiring"]), ("servoo torqe", i["Servo torque"])]
