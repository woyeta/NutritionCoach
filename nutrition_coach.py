"""
The Nutrition Coach tools.

Food lookups call two real, free databases (Open Food Facts for packaged products,
USDA FoodData Central for basic foods), and the arithmetic (daily needs, portion sizes)
is done in Python, so every number the agent reports comes from a tool.
`check_final` enforces that.
"""

import json
import logging
import os
import re
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

OFF_URL = "https://world.openfoodfacts.org"
FDC_URL = "https://api.nal.usda.gov/fdc/v1"
HTTP_TIMEOUT_S = 10
HEADERS = {"User-Agent": "AgentLoop-NutritionCoach/1.0"}  # Open Food Facts asks for one

DISCLAIMER = (
    "\n\nGeneral nutrition information, not medical advice. If you have a health "
    "condition, are pregnant, or are under 18, check with a doctor or registered dietitian."
)

DOCUMENTATION = """Nutrition Coach: answers questions about food, nutrition labels and daily nutrition targets for adults, using real food databases and calculators.

Functions:
1. search_products(query, limit=3): Packaged products from Open Food Facts. `query` is a product name or a barcode. Returns name, brand, Nutri-Score and nutrients per 100 g.
2. search_foods(query, limit=3): Basic foods (e.g. "banana", "cooked brown rice") from USDA FoodData Central. Returns nutrients per 100 g.
3. calculate_daily_needs(sex, age, weight_kg, height_cm, activity, goal="maintain"): Daily calorie target and limits for an adult. sex: "male" or "female". age: 18-100. activity: "sedentary", "light", "moderate", "active" or "very_active". goal: "lose", "maintain" or "gain".
4. scale_to_portion(per_100g, grams): Converts nutrients per 100 g (an object such as {"sugars_g": 12.5, "sodium_mg": 400}) to the amount in a portion of `grams`.

Nutrient keys: energy_kcal, protein_g, fat_g, saturated_fat_g, carbs_g, sugars_g, fiber_g, sodium_mg.

Nutrition rules:
- Every number in the final answer must come from a tool result. Never estimate nutrient values or do arithmetic yourself: use scale_to_portion for servings and calculate_daily_needs for targets.
- If the user mentions a medical condition (e.g. diabetes, kidney disease, high blood pressure), pregnancy, an eating disorder, or is under 18, give general information only and advise them to see a doctor or registered dietitian.
- Do not recommend supplements, medicines or very low-calorie diets."""

NUTRIENT_KEYS = ["energy_kcal", "protein_g", "fat_g", "saturated_fat_g",
                 "carbs_g", "sugars_g", "fiber_g", "sodium_mg"]


# ── Argument schemas ──

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchArgs(_Args):
    query: str = Field(min_length=1)
    limit: int = Field(3, ge=1, le=5)


class DailyNeedsArgs(_Args):
    sex: Literal["male", "female"]
    age: int = Field(ge=18, le=100)
    weight_kg: float = Field(gt=25, lt=350)
    height_cm: float = Field(gt=100, lt=250)
    activity: Literal["sedentary", "light", "moderate", "active", "very_active"]
    goal: Literal["lose", "maintain", "gain"] = "maintain"


class PortionArgs(_Args):
    per_100g: dict[str, float]
    grams: float = Field(gt=0, le=3000)


# ── Food databases ──

def _round(value):
    return None if value is None else round(float(value), 1)


def _off_product(p: dict) -> dict:
    n = p.get("nutriments", {}) or {}
    sodium_g = n.get("sodium_100g")
    nutrients = {
        "energy_kcal": n.get("energy-kcal_100g"),
        "protein_g": n.get("proteins_100g"),
        "fat_g": n.get("fat_100g"),
        "saturated_fat_g": n.get("saturated-fat_100g"),
        "carbs_g": n.get("carbohydrates_100g"),
        "sugars_g": n.get("sugars_100g"),
        "fiber_g": n.get("fiber_100g"),
        "sodium_mg": None if sodium_g is None else float(sodium_g) * 1000,
    }
    return {
        "name": p.get("product_name") or "(unnamed)",
        "brand": p.get("brands") or "",
        "barcode": p.get("code", ""),
        "nutri_score": (p.get("nutriscore_grade") or "unknown").upper(),
        "serving_size": p.get("serving_size") or "",
        "per_100g": {k: _round(v) for k, v in nutrients.items() if v not in (None, "")},
    }


_OFF_FIELDS = "code,product_name,brands,nutriscore_grade,serving_size,nutriments"


def search_products(query: str, limit: int = 3) -> list:
    query = query.strip()
    if re.fullmatch(r"\d{8,14}", query):
        r = requests.get(f"{OFF_URL}/api/v2/product/{query}.json",
                         params={"fields": _OFF_FIELDS}, headers=HEADERS, timeout=HTTP_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        return [_off_product(body["product"])] if body.get("product") else []

    r = requests.get(f"{OFF_URL}/cgi/search.pl", params={
        "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
        "page_size": limit, "fields": _OFF_FIELDS,
    }, headers=HEADERS, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return [_off_product(p) for p in r.json().get("products", [])[:limit]]


# USDA nutrient names → our keys (values are per 100 g for these data types).
_FDC_NUTRIENTS = {
    "Protein": "protein_g",
    "Total lipid (fat)": "fat_g",
    "Fatty acids, total saturated": "saturated_fat_g",
    "Carbohydrate, by difference": "carbs_g",
    "Sugars, total including NLEA": "sugars_g",
    "Total Sugars": "sugars_g",
    "Fiber, total dietary": "fiber_g",
    "Sodium, Na": "sodium_mg",
}


def _fdc_food(food: dict) -> dict:
    per_100g = {}
    for n in food.get("foodNutrients", []) or []:
        name, unit, value = n.get("nutrientName", ""), (n.get("unitName") or "").upper(), n.get("value")
        if value is None:
            continue
        if name.startswith("Energy") and unit == "KCAL":
            per_100g.setdefault("energy_kcal", _round(value))
        elif name in _FDC_NUTRIENTS:
            per_100g.setdefault(_FDC_NUTRIENTS[name], _round(value))
    return {"name": food.get("description", ""), "fdc_id": food.get("fdcId"), "per_100g": per_100g}


def search_foods(query: str, limit: int = 3) -> list:
    r = requests.get(f"{FDC_URL}/foods/search", params={
        "query": query, "pageSize": limit, "dataType": "Foundation,SR Legacy",
        "api_key": os.environ.get("FDC_API_KEY", "DEMO_KEY"),
    }, headers=HEADERS, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return [_fdc_food(f) for f in r.json().get("foods", [])[:limit]]


# ── Calculators ──

_ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "very_active": 1.9}
_MIN_KCAL = {"male": 1500, "female": 1200}


def calculate_daily_needs(sex, age, weight_kg, height_cm, activity, goal="maintain") -> dict:
    """Mifflin-St Jeor BMR × activity factor, with WHO / US guideline limits."""
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)
    maintenance = bmr * _ACTIVITY[activity]
    target = maintenance + {"lose": -500, "maintain": 0, "gain": 300}[goal]
    floor_applied = target < _MIN_KCAL[sex]
    target = max(target, _MIN_KCAL[sex])
    return {
        "bmr_kcal": round(bmr),
        "maintenance_kcal": round(maintenance),
        "target_kcal": round(target),
        "minimum_floor_applied": floor_applied,
        "protein_min_g": round(0.8 * weight_kg),
        "fiber_target_g": round(14 * target / 1000),
        "free_sugars_max_g": round(0.10 * target / 4),
        "saturated_fat_max_g": round(0.10 * target / 9),
        "sodium_max_mg": 2000,
        "basis": "Mifflin-St Jeor; protein 0.8 g/kg; fiber 14 g per 1000 kcal; "
                 "free sugars and saturated fat under 10% of energy; sodium under 2000 mg (WHO)",
    }


def scale_to_portion(per_100g: dict, grams: float) -> dict:
    return {
        "grams": grams,
        "portion": {k: round(v * grams / 100, 1) for k, v in per_100g.items()},
    }


# ── Function table used by the loop ──

FUNCTIONS = {
    "search_products": (SearchArgs, search_products),
    "search_foods": (SearchArgs, search_foods),
    "calculate_daily_needs": (DailyNeedsArgs, calculate_daily_needs),
    "scale_to_portion": (PortionArgs, scale_to_portion),
}



def run(action: str, args: dict) -> tuple[bool, str]:
    """Runs a function with already-validated arguments. Returns (ok, JSON observation)."""
    _, func = FUNCTIONS[action]
    try:
        result = func(**args)
    except requests.RequestException as err:
        logger.warning(f"{action} request failed: {err}")
        return (False, f"Food database request failed: {err}")
    except (KeyError, TypeError, ValueError) as err:
        return (False, f"Could not read the food database response: {err}")

    if isinstance(result, list):
        result = {"results": result} if result else {"results": [], "note": "No matches found. Try a simpler query."}
    return (True, json.dumps(result))


# ── Grounding check ──

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
ERR_MSG_UNGROUNDED = (
    "Error: your final answer contains numbers that are not in any tool result: {numbers}. "
    "Use only numbers from the observations, or call scale_to_portion or "
    "calculate_daily_needs to compute them, then give the final answer again."
)


def _numbers(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _NUMBER.findall(str(text))]


def ungrounded_numbers(final_answer: str, sources: list[str]) -> list[float]:
    """Numbers in the answer that don't match (within rounding) any number in the sources."""
    known = {100.0}  # "per 100 g" is always fine
    for s in sources:
        known.update(_numbers(s))
    missing = []
    for x in _numbers(final_answer):
        if x <= 10 and x.is_integer():
            continue  # counts and list numbering ("2 products", "1.")
        if not any(abs(x - k) <= max(0.5, 0.01 * abs(k)) for k in known):
            missing.append(x)
    return missing


def check_final(final_answer: str, history: list, user_query: str) -> str | None:
    """Returns an error observation if the final answer uses unsupported numbers, else None."""
    sources = [user_query] + [
        str(h.get("observation", "")) for h in history
        if not str(h.get("observation", "")).startswith("Error:")  # skip our own error messages
    ]
    missing = ungrounded_numbers(final_answer, sources)
    if not missing:
        return None
    shown = ", ".join(f"{x:g}" for x in missing[:5])
    return ERR_MSG_UNGROUNDED.format(numbers=shown)
