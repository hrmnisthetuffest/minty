"""Your own foods and recipes, and what can be built from them: protein-gap suggestions, a vegetarian plan, cost per
10 g protein. Only foods in your own list are ever suggested; a small starter list of generic estimates can be adopted
into it on request. Allergies and diet are filters. Body-neutral: protein, fuel and cost only."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from minty_core.health import HealthHub

# generic estimates per serving: (name, serving, kcal, protein g, carbs g, fat g, vegetarian, allergens)
STARTER = [
    ("Dal, cooked (1 bowl)", "1 bowl", 180, 12, 28, 2, True, ""), ("Rajma, cooked (1 bowl)", "1 bowl", 210, 13, 36, 1, True, ""),
    ("Chana, boiled (1 bowl)", "1 bowl", 270, 14.5, 45, 4, True, ""), ("Paneer (100 g)", "100 g", 290, 18, 4, 22, True, "milk"),
    ("Curd (1 bowl, 200 g)", "1 bowl", 120, 7, 9, 5, True, "milk"), ("Milk (1 glass, 250 ml)", "1 glass", 150, 8, 12, 8, True, "milk"),
    ("Egg, boiled", "1 egg", 78, 6.3, 0.6, 5.3, False, "egg"),
    ("Chicken breast, cooked (100 g)", "100 g", 165, 31, 0, 3.6, False, ""),
    ("Soya chunks (50 g dry)", "50 g", 170, 26, 16, 0.5, True, "soy"), ("Tofu (100 g)", "100 g", 80, 8, 2, 4.5, True, "soy"),
    ("Peanuts (30 g)", "30 g", 170, 7.7, 5, 14, True, "peanut"), ("Roti (1)", "1 roti", 100, 3, 18, 2, True, "gluten"),
    ("Rice, cooked (1 bowl)", "1 bowl", 200, 4, 44, 0.4, True, ""),
    ("Whey protein (1 scoop)", "1 scoop", 120, 24, 3, 1.5, True, "milk"),
]


@dataclass
class Totals:
    kcal: float
    protein_g: float
    estimate: bool


@dataclass
class Suggestion:
    food: str
    servings: int
    protein_g: float
    kcal: float
    estimate: bool


@dataclass
class Cost:
    food: str
    cost_minor_per_10g: int


def adopt_starter(hub: HealthHub) -> int:
    have = {f["name"] for f in hub.foods()}
    added = 0
    for name, serving, kcal, protein, carbs, fat, veg, allergens in STARTER:
        if name not in have:
            hub.add_food(name, serving, kcal=kcal, protein_g=protein, carbs_g=carbs, fat_g=fat, vegetarian=veg,
                         allergens=allergens, source="estimate")
            added += 1
    return added


def recipe_totals(hub: HealthHub, name: str) -> Totals | None:
    recipe = next((r for r in hub.recipes() if r["name"].lower() == name.lower()), None)
    if recipe is None:
        return None
    foods = {f["name"]: f for f in hub.foods()}
    kcal = protein = 0.0
    estimate = False
    for food, servings in recipe["items"]:
        f = foods.get(food)
        if f is None:
            continue
        kcal += f["kcal"] * servings
        protein += f["protein_g"] * servings
        estimate = estimate or f["source"] != "you"
    return Totals(round(kcal, 1), round(protein, 1), estimate)


def _allowed(food: dict, allergies: set[str], vegetarian_only: bool) -> bool:
    tags = {t for t in re.split(r"[,\s]+", food["allergens"].lower()) if t}
    return not (tags & {a.lower() for a in allergies}) and (food["vegetarian"] or not vegetarian_only) \
        and food["protein_g"] > 0


def _greedy(hub: HealthHub, target_g: float, allergies: set[str], vegetarian_only: bool, max_foods: int,
            max_servings: int) -> list[Suggestion]:
    pool = sorted((f for f in hub.foods() if _allowed(f, allergies, vegetarian_only)),
                  key=lambda f: -(f["protein_g"] / max(f["kcal"], 1)))
    out, left = [], target_g
    for f in pool:
        if left <= 0 or len(out) >= max_foods:
            break
        n = max(1, min(max_servings, math.ceil(left / f["protein_g"])))
        out.append(Suggestion(f["name"], n, round(f["protein_g"] * n, 1), round(f["kcal"] * n, 1),
                              f["source"] != "you"))
        left -= f["protein_g"] * n
    return out


def protein_gap(hub: HealthHub, gap_g: float, allergies: set[str], vegetarian_only: bool) -> list[Suggestion]:
    return _greedy(hub, gap_g, allergies, vegetarian_only, max_foods=3, max_servings=2)


def vegetarian_plan(hub: HealthHub, target_g: float, allergies: set[str]) -> list[Suggestion]:
    return _greedy(hub, target_g, allergies, True, max_foods=4, max_servings=2)


def cost_per_10g(hub: HealthHub) -> list[Cost]:
    rows = [Cost(f["name"], round(f["cost_minor"] / f["protein_g"] * 10)) for f in hub.foods()
            if f["cost_minor"] is not None and f["protein_g"] > 0]
    return sorted(rows, key=lambda c: c.cost_minor_per_10g)
