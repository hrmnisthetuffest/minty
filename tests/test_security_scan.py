import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("security_scan", Path(__file__).resolve().parents[1] / "scripts" / "security_scan.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def test_a_rule_within_its_accepted_count_passes_and_one_more_fails():
    base = {"B608": 2, "B324": 1}
    assert scan.over_baseline([{"test_id": "B608"}, {"test_id": "B608"}, {"test_id": "B324"}], base) == []
    assert scan.over_baseline([{"test_id": "B608"}] * 3, base) == ["B608: 3 found, 2 accepted"]


def test_a_new_kind_of_finding_is_never_accepted_by_default():
    assert scan.over_baseline([{"test_id": "B602"}], {"B608": 5}) == ["B602: 1 found, 0 accepted"]


def test_the_shipped_baseline_is_a_plain_count_per_rule():
    import json

    base = json.loads(scan.BASELINE.read_text())
    assert base and all(k.startswith("B") and isinstance(v, int) and v > 0 for k, v in base.items())
    assert "B602" not in base and "B605" not in base  # no shell injection findings are accepted
