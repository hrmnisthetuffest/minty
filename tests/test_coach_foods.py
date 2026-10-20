"""Your own foods and recipes, protein-gap suggestions, the vegetarian planner, cost per 10 g protein (877 880 1013 1014)."""
import pytest

from minty_core.coach.foods import (adopt_starter, cost_per_10g, protein_gap, recipe_totals, vegetarian_plan)
from minty_core.health import HealthHub


def hub(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: 1_800_000_000.0)
    h.add_food("Moong dal (1 bowl)", "1 bowl", kcal=180, protein_g=12, carbs_g=28, fat_g=2, vegetarian=True,
               allergens="", cost_minor=1500, source="you")
    h.add_food("Paneer (100 g)", "100 g", kcal=290, protein_g=18, carbs_g=4, fat_g=22, vegetarian=True,
               allergens="milk", cost_minor=4000, source="you")
    h.add_food("Boiled egg", "1 egg", kcal=78, protein_g=6.3, carbs_g=0.6, fat_g=5.3, vegetarian=False,
               allergens="egg", cost_minor=900, source="you")
    h.add_food("Peanuts (30 g)", "30 g", kcal=170, protein_g=7.7, carbs_g=5, fat_g=14, vegetarian=True,
               allergens="peanut", cost_minor=500, source="estimate")
    return h


def test_foods_are_kept_per_serving_and_labelled_by_source(tmp_path):
    h = hub(tmp_path)
    names = {f["name"]: f for f in h.foods()}
    assert names["Boiled egg"]["source"] == "you" and names["Peanuts (30 g)"]["source"] == "estimate"
    h.add_food("Boiled egg", "1 egg", kcal=80, protein_g=6.5, carbs_g=0.6, fat_g=5.3, vegetarian=False,
               allergens="egg", cost_minor=1000, source="you")  # a correction replaces the entry
    assert [f for f in h.foods() if f["name"] == "Boiled egg"][0]["protein_g"] == 6.5 and len(h.foods()) == 4


def test_a_recipe_adds_up_its_ingredients_and_says_when_part_of_it_is_an_estimate(tmp_path):
    h = hub(tmp_path)
    h.add_recipe("Dal and peanuts", [("Moong dal (1 bowl)", 2), ("Peanuts (30 g)", 1)])
    t = recipe_totals(h, "Dal and peanuts")
    assert t.kcal == 530 and t.protein_g == pytest.approx(31.7) and t.estimate is True
    h.add_recipe("Two dal", [("Moong dal (1 bowl)", 2)])
    assert recipe_totals(h, "Two dal").estimate is False
    assert recipe_totals(h, "missing") is None


def test_protein_gap_suggestions_come_only_from_your_own_list_and_skip_allergens(tmp_path):
    h = hub(tmp_path)
    s = protein_gap(h, gap_g=20, allergies={"peanut"}, vegetarian_only=False)
    names = [x.food for x in s]
    assert names and set(names) <= {f["name"] for f in h.foods()}
    assert "Peanuts (30 g)" not in names
    assert sum(x.protein_g for x in s) >= 20 - 1e-9 or len(s) == 3
    none = protein_gap(HealthHub(tmp_path / "empty.db"), 20, set(), False)
    assert none == []  # nothing of yours to suggest from


def test_the_vegetarian_planner_uses_vegetarian_foods_only(tmp_path):
    h = hub(tmp_path)
    plan = vegetarian_plan(h, target_g=40, allergies=set())
    assert plan and all(p.food != "Boiled egg" for p in plan)
    assert sum(p.protein_g for p in plan) >= 40 or len(plan) == 4
    nutty = vegetarian_plan(h, target_g=40, allergies={"milk", "peanut"})
    assert {p.food for p in nutty} == {"Moong dal (1 bowl)"}


def test_cost_per_ten_grams_of_protein_ranks_cheapest_first(tmp_path):
    h = hub(tmp_path)
    ranked = cost_per_10g(h)
    assert [r.food for r in ranked][0] == "Peanuts (30 g)" or ranked[0].cost_minor_per_10g <= ranked[-1].cost_minor_per_10g
    egg = next(r for r in ranked if r.food == "Boiled egg")
    assert egg.cost_minor_per_10g == round(900 / 6.3 * 10)  # Rs 14.29 for 10 g
    h.add_food("Mystery bar", "1", kcal=200, protein_g=10, carbs_g=20, fat_g=8, vegetarian=True, allergens="",
               cost_minor=None, source="estimate")
    assert "Mystery bar" not in [r.food for r in cost_per_10g(h)]  # no price, no figure


def test_the_starter_list_is_adopted_only_on_request_and_labelled_as_estimates(tmp_path):
    h = HealthHub(tmp_path / "s.db")
    assert h.foods() == []
    n = adopt_starter(h)
    foods = h.foods()
    assert n == len(foods) >= 10 and {f["source"] for f in foods} == {"estimate"}
    assert adopt_starter(h) == 0  # a second time adds nothing
