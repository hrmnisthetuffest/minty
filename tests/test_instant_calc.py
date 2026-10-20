import pytest

from minty_core.instant.calc import evaluate, TooBig, from_roman, to_roman
from tests.instant_fakes import engine, say

E = engine()


@pytest.mark.parametrize("said, reply", [
    ("what is 12 plus 7", "12 plus 7 is 19."), ("twelve minus five", "12 minus 5 is 7."), ("what's 9 times 8", "9 times 8 is 72."),
    ("144 divided by 12", "144 divided by 12 is 12."), ("100 / 8", "100 divided by 8 is 12.5."), ("15 mod 4", "15 mod 4 is 3."),
    ("2 to the power of 10", "2 to the power of 10 is 1,024."), ("7 squared", "7 squared is 49."), ("what is 3 cubed", "3 cubed is 27."),
    ("5+5", "5 plus 5 is 10."), ("what is (2 + 3) * 4", "That's 20."), ("10 minus 4 times 2", "10 minus 4 times 2 is 2."),
    ("what is one hundred twenty five plus seventy five", "125 plus 75 is 200."), ("multiply 6 by 7", "6 times 7 is 42."),
    ("add 5 and 3", "5 plus 3 is 8."), ("subtract 3 from 10", "10 minus 3 is 7."), ("divide 20 by 4", "20 divided by 4 is 5."),
    ("what is 1.5 times 4", "1.5 times 4 is 6."), ("what is 10 divided by 3", "10 divided by 3 is 3.33333."),
    ("calculate 99 times 99", "99 times 99 is 9,801."), ("what is minus 5 plus 2", "minus 5 plus 2 is -3."),
    ("what is 1 plus 2 plus 3 plus 4 plus 5", "That's 15."),
])
def test_arithmetic(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("what is 5 divided by 0", "You can't divide by zero."), ("9 to the power of 9999", "That number is far too big to say."),
    ("what is 10 to the power of 400", "That number is far too big to say."),
])
def test_impossible_sums_get_an_honest_answer(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said", ["2026-10-20", "555 1234", "5 apples plus 3 pears", "what is 5 plus", "what is plus 5", "__import__('os')",
                                  "what is 2 plus abc", "what is 2 ** 2 ** 2 ** 2 ** 2 ** 2 ** 9"])
def test_things_that_are_not_sums_are_not_calculated(said):
    reply = say(E, said)
    assert reply is None or reply in ("That number is far too big to say.",)


@pytest.mark.parametrize("said, reply", [
    ("what's 15 percent of 240", "15 percent of 240 is 36."), ("20% of 150", "20 percent of 150 is 30."),
    ("what percent of 80 is 20", "20 is 25 percent of 80."), ("30 is what percent of 120", "30 is 25 percent of 120."),
    ("what percent is 1 of 3", "1 is 33.3333 percent of 3."), ("what percent is 5 of 0", "A percentage of zero doesn't mean anything."),
    ("increase 200 by 10 percent", "200 plus 10 percent is 220."), ("reduce 80 by 25 percent", "80 minus 25 percent is 60."),
    ("add 10 percent to 50", "50 plus 10 percent is 55."), ("subtract 20 percent from 400", "400 minus 20 percent is 320."),
    ("20 percent off 1500", "20 percent off 1,500 is 1,200, so you save 300."),
    ("15 percent tip on 840", "A 15 percent tip on 840 is 126, so 966 in total."),
    ("18 percent gst on 1000", "18 percent GST on 1,000 is 180, so 1,180 in total."),
    ("add gst to 500 at 5 percent", "5 percent GST on 500 is 25, so 525 in total."),
    ("split 1200 between 4 people", "1,200 split 4 ways is 300 each."), ("split the bill of 1000 among 3", "1,000 split 3 ways is 333.33 each."),
])
def test_percentages_and_money(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("square root of 144", "The square root of 144 is 12."), ("square root of 2", "The square root of 2 is 1.41421."),
    ("cube root of 27", "The cube root of 27 is 3."), ("cube root of -8", "The cube root of -8 is -2."),
    ("5 factorial", "5 factorial is 120."), ("20 factorial", "20 factorial is 2.43 times 10 to the 18."), ("10 factorial", "10 factorial is 3,628,800."),
    ("500 factorial", "That factorial is far too big to say."), ("absolute value of -7", "The absolute value of -7 is 7."),
    ("round 3.14159 to 2 decimals", "3.14159 rounded is 3.14."), ("round up 3.2", "3.2 rounded up is 4."),
    ("round down 3.9", "3.9 rounded down is 3."), ("round 1234 to the nearest hundred", "1,234 to the nearest 100 is 1,200."),
    ("half of 50", "Half of 50 is 25."), ("a third of 90", "A third of 90 is 30."), ("a quarter of 10", "A quarter of 10 is 2.5."),
    ("double 16", "Double 16 is 32."), ("halve 9", "Halve 9 is 4.5."), ("log of 100", "The log of 100 in base 10 is 2."),
    ("log base 2 of 8", "The log of 8 in base 2 is 3."), ("natural log of 1", "The natural log of 1 is 0."),
    ("log of 0", "You can only take the log of a positive number."), ("sine of 30 degrees", "The sine of 30 degrees is 0.5."),
    ("cos of 60", "The cosine of 60 degrees is 0.5."), ("tan of 90", "The tangent of that angle is undefined."),
    ("what is pi", "Pi is about 3.14159265."),
])
def test_one_step_maths(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("is 17 prime", "Yes, 17 is prime."), ("is 91 a prime number", "No, 91 is not prime. It divides by 7."), ("is 1 prime", "No, 1 is not prime."),
    ("is 2 prime", "Yes, 2 is prime."), ("is 1000000007 prime", "Yes, 1000000007 is prime."),
    ("factors of 36", "The factors of 36 are 1, 2, 3, 4, 6, 9, 12, 18 and 36."), ("factors of 7", "The factors of 7 are 1 and 7."),
    ("prime factors of 84", "84 is 2 times 2 times 3 times 7."), ("prime factorization of 13", "13 is prime."),
    ("gcd of 12 and 18", "The greatest common divisor of 12 and 18 is 6."), ("hcf of 8 and 12", "The greatest common divisor of 8 and 12 is 4."),
    ("lcm of 4 and 6", "The least common multiple of 4 and 6 is 12."), ("is 12 even", "Yes, 12 is even."), ("is 7 even", "No, 7 is odd."),
    ("is 7 an odd number", "Yes, 7 is odd."),
    ("average of 4, 8 and 15", "The average of 4, 8 and 15 is 9."), ("sum of 3 5 7", "The sum of 3, 5 and 7 is 15."),
    ("median of 1, 5, 9", "The median of 1, 5 and 9 is 5."), ("median of 1 2 3 4", "The median of 1, 2, 3 and 4 is 2.5."),
    ("product of 2, 3 and 4", "The product of 2, 3 and 4 is 24."), ("range of 3 8 20", "The range of 3, 8 and 20 is 17."),
    ("the smallest of 4 9 2", "The smallest of 4, 9 and 2 is 2."),
])
def test_whole_numbers_and_lists(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("255 in binary", "255 in binary is 11111111."), ("hex of 255", "255 in hexadecimal is FF."), ("octal of 64", "64 in octal is 100."),
    ("binary 1010 to decimal", "Binary 1010 is 10 in decimal."), ("hexadecimal ff to decimal", "Hexadecimal FF is 255 in decimal."),
    ("roman numeral for 1994", "1994 in Roman numerals is MCMXCIV."), ("roman numeral for 4000", "Roman numerals only go up to 3999."),
    ("what is XIV in numbers", "XIV is 14."), ("7 times table", "The 7 times table: 7, 14, 21, 28, 35, 42, 49, 56, 63, 70."),
])
def test_number_systems(said, reply):
    assert say(E, said) == reply


def test_roman_numerals_round_trip_and_reject_nonsense():
    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))
    assert from_roman("IIII") is None and from_roman("VX") is None and from_roman("") is None


@pytest.mark.parametrize("said, reply", [
    ("area of a circle with radius 7", "The area of a circle with radius 7 is 153.938."),
    ("circumference of a circle with diameter 10", "The circumference of a circle with diameter 10 is 31.4159."),
    ("area of a rectangle 5 by 8", "The area of a 5 by 8 rectangle is 40."), ("perimeter of a rectangle 5 by 8", "The perimeter of a 5 by 8 rectangle is 26."),
    ("area of a square with side 6", "The area of a square with side 6 is 36."), ("perimeter of a square with side 6", "The perimeter of a square with side 6 is 24."),
    ("area of a triangle base 6 height 4", "The area of that triangle is 12."),
    ("volume of a sphere with radius 3", "The volume of a sphere with radius 3 is 113.097."),
    ("volume of a cube with side 4", "The volume of a cube with side 4 is 64."), ("hypotenuse of 3 and 4", "The hypotenuse is 5."),
])
def test_shapes(said, reply):
    assert say(E, said) == reply


def test_the_calculator_only_does_arithmetic():
    assert evaluate("2 + 3 * 4") == 14 and evaluate("2 ** 10") == 1024 and evaluate("7 % 4") == 3 and evaluate("-3 + 5") == 2
    for bad in ("__import__('os').system('x')", "abs(3)", "[1]", "1 if 2 else 3", "a + 1", "2 ** 100000", "'x' * 5"):
        with pytest.raises((ValueError, SyntaxError, TooBig)):
            evaluate(bad)
