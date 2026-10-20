"""579 verified tutor: the numbers and algebra it states are checked exactly before they are trusted."""
import pytest

from minty_core.study.verify import VerifyError, check, evaluate, solve, verify_text

# (what is asked, what the tutor claims, is the claim right?)
FIXTURES = [
    ("7 * 8", "56", True), ("7 * 8", "54", False),
    ("144 / 12", "12", True), ("144 / 12", "14", False),
    ("3/4 + 1/6", "11/12", True), ("3/4 + 1/6", "4/10", False),
    ("2^10", "1024", True), ("2^10", "1000", False),
    ("15% of 80", "12", True), ("15% of 80", "15", False),
    ("(3 + 4) * 5 - 2", "33", True), ("(3 + 4) * 5 - 2", "35", False),
    ("0.1 + 0.2", "0.3", True), ("1/3 + 1/3 + 1/3", "1", True),
    ("sqrt(144)", "12", True), ("sqrt(50)", "7", False),
    ("-3 * -4", "12", True), ("-3 * -4", "-12", False),
    ("(x + 1)^2", "x^2 + 2x + 1", True), ("(x + 1)^2", "x^2 + 1", False),
    ("2(x + 3)", "2x + 6", True), ("2(x + 3)", "2x + 3", False),
    ("(x - 2)(x + 2)", "x^2 - 4", True), ("(x - 2)(x + 2)", "x^2 + 4", False),
    ("3x + 2x", "5x", True), ("3x + 2x", "6x", False),
    ("1,000 * 3", "3000", True), ("100 - 37", "63", True), ("100 - 37", "73", False),
    ("12 / 8", "1.5", True),
]


@pytest.mark.parametrize("expr,claimed,right", FIXTURES, ids=[f"{e} = {c}" for e, c, _ in FIXTURES])
def test_each_claim_is_verified_or_flagged(expr, claimed, right):
    v = check(expr, claimed)
    assert v.ok is right, v.message
    if not right:
        assert "not" in v.message.lower() and v.expected  # says what it should have been


def test_there_are_thirty_fixtures():
    assert len(FIXTURES) == 30


EQUATIONS = [
    ("2x + 3 = 11", "x = 4", True), ("2x + 3 = 11", "x = 5", False),
    ("5x - 7 = 3x + 9", "8", True), ("x/2 + 1 = 4", "x = 6", True),
    ("x^2 - 5x + 6 = 0", "x = 2 or x = 3", True), ("x^2 - 5x + 6 = 0", "2", False),
    ("x^2 = 49", "7, -7", True), ("x^2 + 1 = 0", "no real solutions", True),
    ("x^2 - 2 = 0", "x = 1.4142135 or x = -1.4142135", True),
    ("x^2 - 2 = 0", "x = 1.4142135", False),
]


@pytest.mark.parametrize("eq,claimed,right", EQUATIONS)
def test_equations_are_solved_exactly_and_claims_checked(eq, claimed, right):
    assert check(eq, claimed).ok is right


def test_solutions_come_back_exact():
    assert solve("2x + 3 = 11") == ["x = 4"]
    assert solve("x^2 - 5x + 6 = 0") == ["x = 2", "x = 3"]
    assert solve("3x = 1") == ["x = 1/3"]
    assert solve("x^2 + 1 = 0") == []
    assert solve("x + 1 = x + 2") == []  # no solution
    assert solve("2(x + 1) = 2x + 2")[0].startswith("every")  # always true


def test_a_text_is_scanned_and_only_the_wrong_statements_are_flagged():
    text = "First 7 × 8 = 56. Then 56 + 6 = 63, and 2x + 3 = 11 so x = 4. Also 9 * 9 = 81."
    flags = verify_text(text)
    assert [f.statement for f in flags] == ["56 + 6 = 63"]
    assert "62" in flags[0].expected


def test_text_with_no_arithmetic_has_nothing_to_flag():
    assert verify_text("The mitochondria is the powerhouse of the cell.") == []


def test_unsafe_or_unsupported_input_is_refused_not_executed():
    for bad in ("__import__('os').system('x')", "2 ** 9999999", "open('f')", "x ** y", "1/0", "sin(30)", "a.b"):
        with pytest.raises(VerifyError):
            evaluate(bad)
    assert check("1/0", "0").ok is None


def test_unverifiable_claims_say_they_could_not_be_verified():
    v = check("the area of the circle", "12")
    assert v.ok is None and "could not" in v.message.lower()
