"""Unit conversions: length, weight, volume, speed, area, time, data, angle and temperature."""
from __future__ import annotations

import re

from minty_core.instant.base import Query, Rule
from minty_core.instant.text import num

CATEGORY = "conversions"
# dimension, key, size in the dimension's base unit, singular, plural, spoken aliases
_TABLE = [
    ("length", "mm", 0.001, "millimetre", "millimetres", ("millimeter", "millimeters", "millimetre", "millimetres", "mm")),
    ("length", "cm", 0.01, "centimetre", "centimetres", ("centimeter", "centimeters", "centimetre", "centimetres", "cm")),
    ("length", "m", 1, "metre", "metres", ("meter", "meters", "metre", "metres", "m")),
    ("length", "km", 1000, "kilometre", "kilometres", ("kilometer", "kilometers", "kilometre", "kilometres", "km", "kms", "klick", "klicks")),
    ("length", "in", 0.0254, "inch", "inches", ("inch", "inches", "in.")),
    ("length", "ft", 0.3048, "foot", "feet", ("foot", "feet", "ft")),
    ("length", "yd", 0.9144, "yard", "yards", ("yard", "yards", "yd", "yds")),
    ("length", "mi", 1609.344, "mile", "miles", ("mile", "miles", "mi")),
    ("length", "nmi", 1852, "nautical mile", "nautical miles", ("nautical mile", "nautical miles")),
    ("mass", "mg", 1e-6, "milligram", "milligrams", ("milligram", "milligrams", "mg")),
    ("mass", "g", 0.001, "gram", "grams", ("gram", "grams", "g", "gm", "gms")),
    ("mass", "kg", 1, "kilogram", "kilograms", ("kilogram", "kilograms", "kg", "kgs", "kilo", "kilos")),
    ("mass", "t", 1000, "tonne", "tonnes", ("tonne", "tonnes", "metric ton", "metric tons", "ton", "tons")),
    ("mass", "oz", 0.028349523125, "ounce", "ounces", ("ounce", "ounces", "oz")),
    ("mass", "lb", 0.45359237, "pound", "pounds", ("pound", "pounds", "lb", "lbs")),
    ("mass", "st", 6.35029318, "stone", "stone", ("stone", "stones")),
    ("volume", "ml", 0.001, "millilitre", "millilitres", ("millilitre", "millilitres", "milliliter", "milliliters", "ml", "cc")),
    ("volume", "l", 1, "litre", "litres", ("litre", "litres", "liter", "liters", "l")),
    ("volume", "tsp", 0.00492892159375, "teaspoon", "teaspoons", ("teaspoon", "teaspoons", "tsp")),
    ("volume", "tbsp", 0.01478676478125, "tablespoon", "tablespoons", ("tablespoon", "tablespoons", "tbsp")),
    ("volume", "floz", 0.0295735295625, "fluid ounce", "fluid ounces", ("fluid ounce", "fluid ounces", "fl oz", "floz")),
    ("volume", "cup", 0.2365882365, "cup", "cups", ("cup", "cups")),
    ("volume", "pt", 0.473176473, "pint", "pints", ("pint", "pints")),
    ("volume", "qt", 0.946352946, "quart", "quarts", ("quart", "quarts")),
    ("volume", "gal", 3.785411784, "gallon", "gallons", ("gallon", "gallons", "gal")),
    ("speed", "ms", 1, "metre per second", "metres per second", ("metre per second", "metres per second", "meter per second",
                                                                "meters per second", "m/s", "mps")),
    ("speed", "kmh", 1 / 3.6, "kilometre per hour", "kilometres per hour",
     ("kilometre per hour", "kilometres per hour", "kilometer per hour", "kilometers per hour", "km/h", "kmh", "kph", "km per hour")),
    ("speed", "mph", 0.44704, "mile per hour", "miles per hour", ("mile per hour", "miles per hour", "mph")),
    ("speed", "kn", 1852 / 3600, "knot", "knots", ("knot", "knots", "kt", "kts")),
    ("speed", "fps", 0.3048, "foot per second", "feet per second", ("foot per second", "feet per second", "ft/s", "fps")),
    ("area", "m2", 1, "square metre", "square metres", ("square metre", "square metres", "square meter", "square meters", "sq m", "sqm", "m2")),
    ("area", "cm2", 1e-4, "square centimetre", "square centimetres", ("square centimetre", "square centimetres", "square centimeter",
                                                                      "square centimeters", "sq cm", "cm2")),
    ("area", "km2", 1e6, "square kilometre", "square kilometres", ("square kilometre", "square kilometres", "square kilometer",
                                                                  "square kilometers", "sq km", "km2")),
    ("area", "ha", 1e4, "hectare", "hectares", ("hectare", "hectares", "ha")),
    ("area", "acre", 4046.8564224, "acre", "acres", ("acre", "acres")),
    ("area", "ft2", 0.09290304, "square foot", "square feet", ("square foot", "square feet", "sq ft", "sqft", "ft2")),
    ("area", "in2", 6.4516e-4, "square inch", "square inches", ("square inch", "square inches", "sq in")),
    ("area", "mi2", 2589988.110336, "square mile", "square miles", ("square mile", "square miles", "sq mi")),
    ("time", "ms_", 0.001, "millisecond", "milliseconds", ("millisecond", "milliseconds")),
    ("time", "s", 1, "second", "seconds", ("second", "seconds", "sec", "secs")),
    ("time", "min", 60, "minute", "minutes", ("minute", "minutes", "min", "mins")),
    ("time", "h", 3600, "hour", "hours", ("hour", "hours", "hr", "hrs")),
    ("time", "d", 86400, "day", "days", ("day", "days")),
    ("time", "wk", 604800, "week", "weeks", ("week", "weeks")),
    ("time", "fortnight", 1209600, "fortnight", "fortnights", ("fortnight", "fortnights")),
    ("time", "yr", 31536000, "year", "years", ("year", "years", "yr", "yrs")),
    ("time", "decade", 315360000, "decade", "decades", ("decade", "decades")),
    ("time", "century", 3153600000, "century", "centuries", ("century", "centuries")),
    ("data", "bit", 0.125, "bit", "bits", ("bit", "bits")),
    ("data", "B", 1, "byte", "bytes", ("byte", "bytes")),
    ("data", "KB", 1e3, "kilobyte", "kilobytes", ("kilobyte", "kilobytes", "kb")),
    ("data", "MB", 1e6, "megabyte", "megabytes", ("megabyte", "megabytes", "mb", "megs", "meg")),
    ("data", "GB", 1e9, "gigabyte", "gigabytes", ("gigabyte", "gigabytes", "gb", "gig", "gigs")),
    ("data", "TB", 1e12, "terabyte", "terabytes", ("terabyte", "terabytes", "tb")),
    ("data", "KiB", 1024, "kibibyte", "kibibytes", ("kibibyte", "kibibytes", "kib")),
    ("data", "MiB", 1024**2, "mebibyte", "mebibytes", ("mebibyte", "mebibytes", "mib")),
    ("data", "GiB", 1024**3, "gibibyte", "gibibytes", ("gibibyte", "gibibytes", "gib")),
    ("angle", "deg", 1, "degree", "degrees", ("degree", "degrees", "deg")),
    ("angle", "rad", 57.29577951308232, "radian", "radians", ("radian", "radians", "rad")),
    ("temperature", "C", 0, "degree Celsius", "degrees Celsius", ("celsius", "centigrade", "degree celsius", "degrees celsius",
                                                                 "degrees centigrade", "c")),
    ("temperature", "F", 0, "degree Fahrenheit", "degrees Fahrenheit", ("fahrenheit", "degree fahrenheit", "degrees fahrenheit", "f")),
    ("temperature", "K", 0, "kelvin", "kelvin", ("kelvin", "kelvins")),
]
UNITS = {row[1]: row for row in _TABLE}
ALIASES = {alias: row[1] for row in _TABLE for alias in row[5]}
_DECIMAL_DATA = {"KB", "MB", "GB", "TB", "B"}
N = r"(-?\d+(?:\.\d+)?)"


def find_unit(text: str) -> str | None:
    t = " ".join(text.lower().replace("°", " ").replace("degrees of", "degrees").split())
    t = re.sub(r"^(?:an?|the|one|some)\s+", "", t)
    for candidate in (t, t[:-1] if t.endswith("s") else t, t[:-2] if t.endswith("es") else t,
                      re.sub(r"^(?:degrees?|deg) ", "", t)):
        if candidate in ALIASES:
            return ALIASES[candidate]
    return None


def _to_celsius(value: float, key: str) -> float:
    return value if key == "C" else (value - 32) * 5 / 9 if key == "F" else value - 273.15


def _from_celsius(value: float, key: str) -> float:
    return value if key == "C" else value * 9 / 5 + 32 if key == "F" else value + 273.15


def convert(value: float, a: str, b: str) -> float | None:
    da, db = UNITS[a][0], UNITS[b][0]
    if da != db:
        return None
    if da == "temperature":
        return _from_celsius(_to_celsius(value, a), b)
    return value * UNITS[a][2] / UNITS[b][2]


def _name(key: str, value: float) -> str:
    return UNITS[key][3] if value == 1 else UNITS[key][4]


def _sentence(value: float, a: str, b: str) -> str | None:
    result = convert(value, a, b)
    if result is None:
        return None
    if UNITS[a][0] == "temperature" and a == b:
        result = value
    text = f"{num(value)} {_name(a, value)} is {num(result)} {_name(b, result)}."
    if a in _DECIMAL_DATA and b in _DECIMAL_DATA and a != b:
        text += " Some systems count in 1,024s instead."
    return text


_LEAD = re.compile(r"^(?:what is|what are|how much is|how many is|convert|change)\s+")
_HOW_MANY = re.compile(r"^how (?:many|much) (.+?) (?:are |is )?(?:there )?(?:in|per|to) (.+)$")
_PLAIN = re.compile(rf"^{N} ?(.+?) (?:to|in|into|as|equals?|equal to) (?:the )?(.+)$")


def conversions(q: Query) -> str | None:
    d = _LEAD.sub("", q.digits)
    m = _HOW_MANY.match(q.digits)
    if m:
        b_text = m[2]
        count = 1.0
        lead = re.match(rf"^{N} (.+)$", b_text)
        if lead:
            count, b_text = float(lead[1]), lead[2]
        a, b = find_unit(m[1]), find_unit(b_text)
        if a is None or b is None or UNITS[a][0] == "temperature":
            return None
        return _sentence(count, b, a)
    m = _PLAIN.match(d)
    if m:
        a, b = find_unit(m[2]), find_unit(m[3])
        if a is None or b is None:
            return None
        return _sentence(float(m[1]), a, b)
    return None


RULES = (
    Rule("unit conversion", CATEGORY, (
        "convert 5 miles to kilometres", "how many km in 5 miles", "100 fahrenheit in celsius", "30 c to f", "what is 10 kg in pounds",
        "how many ounces in a pound", "how many cups in a gallon", "5 feet in centimetres", "how many seconds in a day",
        "how many minutes in 3 hours", "convert 60 mph to km/h", "2 litres in millilitres", "how many teaspoons in a tablespoon",
        "1 gigabyte in megabytes", "how many megabytes in a gigabyte", "180 pounds in kilos", "convert 6 feet to metres",
        "how many square feet in an acre", "50 knots in kilometres per hour", "how many hours in a week", "0 kelvin in celsius",
        "what is 72 degrees fahrenheit in celsius", "how many inches in a foot", "how many grams in an ounce",
        "convert 3 pints to litres", "how many millilitres in a cup", "twenty five celsius to fahrenheit"), conversions),
)
