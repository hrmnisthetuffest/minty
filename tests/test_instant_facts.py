import pytest

from minty_core.instant.facts import BY_NAME, CAPITALS, ELEMENTS, FORMULAS, FORMULA_NAMES, FULL_FORMS
from tests.instant_fakes import engine, say

E = engine()


@pytest.mark.parametrize("said, reply", [
    ("what is the capital of france", "The capital of France is Paris."), ("capital of japan", "The capital of Japan is Tokyo."),
    ("what's the capital of the usa", "The capital of the United States is Washington, D.C."),
    ("what is the capital of the united kingdom", "The capital of the United Kingdom is London."),
    ("what's the capital of india", "The capital of India is New Delhi."), ("capital of australia", "The capital of Australia is Canberra."),
    ("what is paris the capital of", "Paris is the capital of France."), ("canberra is the capital of which country", "Canberra is the capital of Australia."),
    ("which city is the capital of brazil", "The capital of Brazil is Brasilia."),
    ("what is the capital of narnia", None), ("what is the capital of the moon", None),
])
def test_capitals(said, reply):
    assert say(E, said) == reply


def test_the_capital_table_has_no_blank_entries():
    assert len(CAPITALS) > 150 and all(k == k.lower() and v for k, v in CAPITALS.items())


@pytest.mark.parametrize("said, reply", [
    ("what is the chemical symbol for gold", "The symbol for gold is Au."), ("symbol of iron", "The symbol for iron is Fe."),
    ("atomic number of oxygen", "Oxygen has atomic number 8."), ("what is the atomic number of gold", "Gold has atomic number 79."),
    ("what element has the symbol Fe", "Fe is iron."), ("what does Na stand for in chemistry", "Na is sodium."),
    ("element 26", "Element 26 is iron, symbol Fe."), ("what element has atomic number 6", "Element 6 is carbon, symbol C."),
    ("what is the atomic mass of carbon", "The atomic mass of carbon is about 12.011."),
    ("what is the formula for water", "The formula for water is H2O."), ("what is H2O", "H2O is water."), ("what is NaCl", "NaCl is sodium chloride, or table salt."), ("what is co", None),
    ("what is CO2", "CO2 is carbon dioxide."), ("chemical formula of table salt", "The formula for table salt is NaCl."),
    ("symbol for aluminum", "The symbol for aluminum is Al."), ("element 200", None),
])
def test_chemistry(said, reply):
    assert say(E, said) == reply


def test_the_periodic_table_is_whole_and_in_order():
    assert len(ELEMENTS) == 118 and ELEMENTS[0][1:] == ("H", "Hydrogen") and ELEMENTS[117][1:] == ("Og", "Oganesson")
    assert [e[0] for e in ELEMENTS] == list(range(1, 119))
    assert len({e[1] for e in ELEMENTS}) == 118 and all(e[1][0].isupper() for e in ELEMENTS)
    assert BY_NAME["gold"][1] == "Au" and BY_NAME["mercury"][1] == "Hg" and BY_NAME["sodium"][1] == "Na" and BY_NAME["tungsten"][1] == "W"


def test_formula_tables_agree():
    for name, formula in FORMULAS.items():
        assert formula.lower() in FORMULA_NAMES or name in ("salt", "sugar", "vinegar", "sulphuric acid", "baking soda"), (name, formula)


@pytest.mark.parametrize("said, reply", [
    ("what is the full form of cpu", "CPU stands for Central Processing Unit."), ("ram full form", "RAM stands for Random Access Memory."),
    ("what does usb stand for", "USB stands for Universal Serial Bus."), ("what is gps short for", "GPS stands for Global Positioning System."),
    ("expand json", "JSON stands for JavaScript Object Notation."), ("what does zzz stand for", None),
])
def test_full_forms(said, reply):
    assert say(E, said) == reply


def test_full_forms_are_capitalised_words():
    assert len(FULL_FORMS) > 80 and all(v[0].isupper() for v in FULL_FORMS.values())


@pytest.mark.parametrize("said, reply", [
    ("how many continents are there", "Seven: Asia, Africa, North America, South America, Antarctica, Europe and Australia."),
    ("how many planets are there", "Eight: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus and Neptune, in order from the sun."),
    ("what is the largest planet", "Jupiter is the largest planet."), ("what is the speed of light", "299,792,458 metres per second, about 300,000 kilometres per second."),
    ("what is the boiling point of water", "100 degrees Celsius, or 212 Fahrenheit, at sea level."),
    ("how many bones are in the human body", "An adult has 206 bones."), ("how many zeros in a crore", "7."),
    ("how many lakh in a crore", "100 lakh make a crore."), ("how many sides does a hexagon have", "A hexagon has 6 sides."),
    ("what day comes after friday", "Saturday comes after Friday."), ("what day comes after sunday", "Monday comes after Sunday."),
    ("what month comes before january", "December comes before January."), ("how many letters in necessary", "Necessary has 9 letters."),
    ("how many states in india", "28 states and 8 union territories."), ("what is the tallest mountain", "Mount Everest, at about 8,849 metres above sea level."),
])
def test_constants_and_counts(said, reply):
    assert say(E, said) == reply
