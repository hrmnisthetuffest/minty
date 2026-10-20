import pytest

from minty_core.instant.units import ALIASES, UNITS, convert, find_unit
from tests.instant_fakes import engine, say

E = engine()


@pytest.mark.parametrize("said, reply", [
    ("convert 5 miles to kilometres", "5 miles is 8.04672 kilometres."), ("how many km in 5 miles", "5 miles is 8.04672 kilometres."),
    ("100 fahrenheit in celsius", "100 degrees Fahrenheit is 37.7778 degrees Celsius."), ("30 c to f", "30 degrees Celsius is 86 degrees Fahrenheit."),
    ("0 kelvin in celsius", "0 kelvin is -273.15 degrees Celsius."), ("what is 10 kg in pounds", "10 kilograms is 22.0462 pounds."),
    ("how many ounces in a pound", "1 pound is 16 ounces."), ("how many cups in a gallon", "1 gallon is 16 cups."),
    ("how many teaspoons in a tablespoon", "1 tablespoon is 3 teaspoons."), ("how many seconds in a day", "1 day is 86,400 seconds."),
    ("how many minutes in 3 hours", "3 hours is 180 minutes."), ("how many hours in a week", "1 week is 168 hours."),
    ("how many days in a week", "1 week is 7 days."), ("5 feet in centimetres", "5 feet is 152.4 centimetres."),
    ("how many inches in a foot", "1 foot is 12 inches."), ("2 litres in millilitres", "2 litres is 2,000 millilitres."),
    ("60 mph to km/h", "60 miles per hour is 96.5606 kilometres per hour."),
    ("1 gigabyte in megabytes", "1 gigabyte is 1,000 megabytes. Some systems count in 1,024s instead."),
    ("1 gibibyte in mebibytes", "1 gibibyte is 1,024 mebibytes."), ("how many square feet in an acre", "1 acre is 43,560 square feet."),
    ("twenty five celsius to fahrenheit", "25 degrees Celsius is 77 degrees Fahrenheit."), ("what is 1 degree celsius in kelvin", "1 degree Celsius is 274.15 kelvin."),
    ("180 degrees in radians", "180 degrees is 3.14159 radians."),
])
def test_conversions(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said", [
    "convert 5 miles to kilograms", "5 apples in 2 baskets", "how many people in a bus", "how many days in the holidays",
    "convert 5 to 6", "how many cups in a mile and a half of coffee please", "10 dollars in rupees", "how many calories in a banana",
])
def test_unknown_or_mismatched_units_go_to_the_model(said):
    assert say(E, said) is None


def test_every_alias_points_to_a_unit_and_every_unit_converts_to_itself():
    assert all(key in UNITS for key in ALIASES.values())
    for key, row in UNITS.items():
        assert convert(7.0, key, key) == pytest.approx(7.0), key


def test_aliases_do_not_clash_between_dimensions():
    seen: dict[str, str] = {}
    for row in UNITS.values():
        for alias in row[5]:
            assert seen.setdefault(alias, row[0]) == row[0], alias


@pytest.mark.parametrize("text, key", [("km", "km"), ("kilometers", "kms" and "km"), ("a mile", "mi"), ("degrees celsius", "C"),
                                       ("Fahrenheit", "F"), ("the pound", "lb"), ("pounds", "lb"), ("feet", "ft"), ("sq ft", "ft2")])
def test_unit_words(text, key):
    assert find_unit(text) == key
