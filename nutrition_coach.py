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
import time
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

OFF_URL = "https://world.openfoodfacts.org"
FDC_URL = "https://api.nal.usda.gov/fdc/v1"
HTTP_TIMEOUT_S = 10
HEADERS = {"User-Agent": "NutritionCoach/1.0 (student project)"}  # Open Food Facts asks for one
RETRY_STATUS = {429, 502, 503, 504}  # "busy, try again" (Open Food Facts uses 503 for rate limits)
RETRIES = 2
RETRY_DELAY_S = 2.0

DISCLAIMER = (
    "\n\nGeneral nutrition information, not medical advice. If you have a health "
    "condition, are pregnant, or are under 18, check with a doctor or registered dietitian."
)

DOCUMENTATION = """Nutrition Coach: answers questions about food, nutrition labels and daily nutrition targets for adults, using real food databases and calculators.

Functions:
1. search_products(query, limit=3): Packaged products. `query` is a product name or a barcode. Returns name, brand, nutrients per 100 g, the data source, and when available: Nutri-Score, NOVA processing group, and allergens from the label.
2. search_foods(query, limit=3): Basic foods (e.g. "banana", "cooked brown rice") from USDA FoodData Central. Returns nutrients per 100 g.
3. calculate_daily_needs(sex, age, weight_kg, height_cm, activity, goal="maintain"): Daily calorie target and limits for an adult. sex: "male" or "female". age: 18-100. activity: "sedentary", "light", "moderate", "active" or "very_active". goal: "lose", "maintain" or "gain".
4. scale_to_portion(per_100g, grams, food): Converts nutrients per 100 g (an object such as {"sugars_g": 12.5, "sodium_mg": 400}) to the amount in a portion of `grams`. `food` names the item, e.g. "Coca-Cola", so you can tell results apart.
5. percent_of_limit(amount, limit, label): What percent `amount` is of `limit`, e.g. amount 3.8 (g of saturated fat in a portion) and limit 23 (daily maximum from calculate_daily_needs). `label` says what is being compared, e.g. "saturated fat in 40 g of Nutella".

Nutrient keys: energy_kcal, protein_g, fat_g, saturated_fat_g, carbs_g, sugars_g, fiber_g, sodium_mg, potassium_mg, calcium_mg, iron_mg, magnesium_mg, vitamin_c_mg, vitamin_d_ug. A missing key means the database has no value, not zero.

NOVA processing group: 1 = unprocessed or minimally processed, 2 = processed culinary ingredient, 3 = processed food, 4 = ultra-processed food.

Allergens: "contains" and "may_contain" list what the label declares. The data is crowd-sourced and can be incomplete.

Nutrition rules:
- Every number in the final answer must come from a tool result. Never estimate nutrient values or do arithmetic yourself: use scale_to_portion for servings, calculate_daily_needs for targets, and percent_of_limit for percentages.
- If the user mentions a medical condition (e.g. diabetes, kidney disease, high blood pressure), pregnancy, an eating disorder, or is under 18, give general information only and advise them to see a doctor or registered dietitian.
- Do not recommend supplements, medicines or very low-calorie diets.
- If the query includes a "User profile", use it for personal questions without asking (for example as the calculate_daily_needs arguments). Values given in the question itself take priority. If the profile has no value you need, ask for it in the final answer instead of guessing.
- If the profile lists allergies, say for each product you report whether it lists them under "contains" or "may_contain". If it lists health conditions or pregnancy, follow the medical rule above. Keep the user's diet in mind when suggesting foods.
- Allergens: report only what "contains" and "may_contain" list. Never say a product is free of an allergen or safe for someone with an allergy, even if nothing is listed. Always tell the user to check the package label."""

ALLERGEN_NOTE = (
    "\n\nAllergen information is crowd-sourced and may be incomplete or out of date. Always "
    "check the package label, and don't rely on this app if you have a severe allergy."
)

NOVA_LABELS = {1: "unprocessed or minimally processed", 2: "processed culinary ingredient",
               3: "processed food", 4: "ultra-processed food"}


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


class PercentArgs(_Args):
    amount: float = Field(ge=0)
    limit: float = Field(gt=0)
    label: str = Field("", max_length=100)


class PortionArgs(_Args):
    per_100g: dict[str, float]
    grams: float = Field(gt=0, le=3000)
    food: str = Field("", max_length=100)


# ── Food databases ──

_cache: dict = {}


def _get_json(url: str, params: dict | None = None) -> dict:
    """GET with retries on busy/rate-limit responses and a small in-memory cache."""
    key = (url, json.dumps(params or {}, sort_keys=True))
    if key in _cache:
        return _cache[key]
    for attempt in range(RETRIES + 1):
        last_try = attempt == RETRIES
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT_S)
        except (requests.ConnectionError, requests.Timeout):
            if last_try:
                raise
        else:
            if r.status_code not in RETRY_STATUS or last_try:
                r.raise_for_status()
                body = r.json()
                if len(_cache) > 256:
                    _cache.clear()
                _cache[key] = body
                return body
        time.sleep(RETRY_DELAY_S * (attempt + 1))


def _round(value):
    return None if value is None else round(float(value), 1)


# Open Food Facts nutriment → (our key, multiplier). OFF stores *_100g values in grams.
_OFF_NUTRIENTS = {
    "energy-kcal_100g": ("energy_kcal", 1),
    "proteins_100g": ("protein_g", 1),
    "fat_100g": ("fat_g", 1),
    "saturated-fat_100g": ("saturated_fat_g", 1),
    "carbohydrates_100g": ("carbs_g", 1),
    "sugars_100g": ("sugars_g", 1),
    "fiber_100g": ("fiber_g", 1),
    "sodium_100g": ("sodium_mg", 1000),
    "potassium_100g": ("potassium_mg", 1000),
    "calcium_100g": ("calcium_mg", 1000),
    "iron_100g": ("iron_mg", 1000),
    "magnesium_100g": ("magnesium_mg", 1000),
    "vitamin-c_100g": ("vitamin_c_mg", 1000),
    "vitamin-d_100g": ("vitamin_d_ug", 1_000_000),
}


def _tag_names(tags) -> list[str]:
    """["en:milk", "en:soybeans"] → ["milk", "soybeans"]."""
    return [t.split(":", 1)[-1].replace("-", " ") for t in (tags or []) if isinstance(t, str)]


def _off_product(p: dict) -> dict:
    n = p.get("nutriments", {}) or {}
    per_100g = {}
    for off_key, (key, factor) in _OFF_NUTRIENTS.items():
        value = n.get(off_key)
        if value in (None, ""):
            continue
        try:
            per_100g[key] = _round(float(value) * factor)
        except (TypeError, ValueError):
            pass  # crowd-sourced data: skip unreadable values

    product = {
        "name": p.get("product_name") or "(unnamed)",
        "brand": p.get("brands") or "",
        "barcode": p.get("code", ""),
        "nutri_score": (p.get("nutriscore_grade") or "unknown").upper(),
        "serving_size": p.get("serving_size") or "",
        "per_100g": per_100g,
    }
    if p.get("nova_group") in NOVA_LABELS:
        product["nova_group"] = p["nova_group"]
        product["nova_label"] = NOVA_LABELS[p["nova_group"]]
    product["allergens"] = {
        "contains": _tag_names(p.get("allergens_tags")),
        "may_contain": _tag_names(p.get("traces_tags")),
        "note": "From the label as entered by volunteers; may be incomplete. Check the package.",
    }
    product["source"] = "Open Food Facts"
    return product


_OFF_FIELDS = "code,product_name,brands,nutriscore_grade,nova_group,serving_size,nutriments,allergens_tags,traces_tags"


def search_products(query: str, limit: int = 3) -> list:
    """
    Open Food Facts by barcode or name. Name search is rate-limited and often busy, so if it
    still fails after retries, falls back to USDA's branded (packaged) foods.
    """
    query = query.strip()
    if re.fullmatch(r"\d{8,14}", query):
        body = _get_json(f"{OFF_URL}/api/v2/product/{query}.json", {"fields": _OFF_FIELDS})
        return [_off_product(body["product"])] if body.get("product") else []

    try:
        body = _get_json(f"{OFF_URL}/cgi/search.pl", {
            "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
            "page_size": limit, "fields": _OFF_FIELDS,
        })
        return [_off_product(p) for p in body.get("products", [])[:limit]]
    except requests.RequestException as err:
        logger.warning(f"Open Food Facts search failed ({err}); falling back to USDA branded foods")
        return _fdc_search(query, limit, "Branded")


# USDA nutrient names → our keys (values are per 100 g for these data types).
# USDA nutrient name → (our key, required unit). The unit check skips e.g. vitamin D in IU.
_FDC_NUTRIENTS = {
    "Protein": ("protein_g", "G"),
    "Total lipid (fat)": ("fat_g", "G"),
    "Fatty acids, total saturated": ("saturated_fat_g", "G"),
    "Carbohydrate, by difference": ("carbs_g", "G"),
    "Sugars, total including NLEA": ("sugars_g", "G"),
    "Total Sugars": ("sugars_g", "G"),
    "Fiber, total dietary": ("fiber_g", "G"),
    "Sodium, Na": ("sodium_mg", "MG"),
    "Potassium, K": ("potassium_mg", "MG"),
    "Calcium, Ca": ("calcium_mg", "MG"),
    "Iron, Fe": ("iron_mg", "MG"),
    "Magnesium, Mg": ("magnesium_mg", "MG"),
    "Vitamin C, total ascorbic acid": ("vitamin_c_mg", "MG"),
    "Vitamin D (D2 + D3)": ("vitamin_d_ug", "UG"),
}


def _fdc_food(food: dict) -> dict:
    per_100g = {}
    for n in food.get("foodNutrients", []) or []:
        name, unit, value = n.get("nutrientName", ""), (n.get("unitName") or "").upper(), n.get("value")
        if value is None:
            continue
        if name.startswith("Energy") and unit == "KCAL":
            per_100g.setdefault("energy_kcal", _round(value))
        elif name in _FDC_NUTRIENTS and unit == _FDC_NUTRIENTS[name][1]:
            per_100g.setdefault(_FDC_NUTRIENTS[name][0], _round(value))
    food_info = {"name": food.get("description", ""), "fdc_id": food.get("fdcId")}
    if food.get("brandOwner") or food.get("brandName"):  # branded (packaged) food
        food_info["brand"] = food.get("brandName") or food.get("brandOwner")
        food_info["barcode"] = food.get("gtinUpc", "")
        food_info["allergens"] = "Not provided by this source. Check the ingredients on the package."
    food_info["per_100g"] = per_100g
    food_info["source"] = "USDA FoodData Central"
    return food_info


def _fdc_search(query: str, limit: int, data_type: str) -> list:
    body = _get_json(f"{FDC_URL}/foods/search", {
        "query": query, "pageSize": limit, "dataType": data_type,
        "api_key": os.environ.get("FDC_API_KEY", "DEMO_KEY"),
    })
    return [_fdc_food(f) for f in body.get("foods", [])[:limit]]


def search_foods(query: str, limit: int = 3) -> list:
    return _fdc_search(query, limit, "Foundation,SR Legacy")


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


def percent_of_limit(amount: float, limit: float, label: str = "") -> dict:
    return {
        "label": label or "(not named)",
        "amount": amount,
        "limit": limit,
        "percent_of_limit": round(amount / limit * 100, 1),
    }


def scale_to_portion(per_100g: dict, grams: float, food: str = "") -> dict:
    return {
        "food": food or "(not named)",
        "grams": grams,
        "portion": {k: round(v * grams / 100, 1) for k, v in per_100g.items()},
    }


# ── Function table used by the loop ──

FUNCTIONS = {
    "search_products": (SearchArgs, search_products),
    "search_foods": (SearchArgs, search_foods),
    "calculate_daily_needs": (DailyNeedsArgs, calculate_daily_needs),
    "scale_to_portion": (PortionArgs, scale_to_portion),
    "percent_of_limit": (PercentArgs, percent_of_limit),
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

    if isinstance(result, list):  # searches: say what was searched for
        result = {"query": args["query"], "results": result}
        if not result["results"]:
            result["note"] = "No matches found. Try a simpler query."
    return (True, json.dumps(result))


# ── Final-answer checks ──

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
ERR_MSG_UNGROUNDED = (
    "Error: your final answer contains numbers that are not in any tool result: {numbers}. "
    "Use only numbers from the observations, or call scale_to_portion, "
    "calculate_daily_needs or percent_of_limit to compute them, then give the final answer again."
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


_ALLERGENS = (r"(?:allergens?|nuts?|peanuts?|tree nuts?|gluten|wheat|milk|dairy|lactose|eggs?|soy|soya|"
              r"sesame|fish|shellfish|crustaceans?|molluscs?|mustard|celery|lupin|sulph?ites?)")
_ALLERGEN_FREE_CLAIM = re.compile(
    rf"\b(?:{_ALLERGENS}[- ]free"
    rf"|free (?:of|from) (?:any |all )?{_ALLERGENS}"
    rf"|(?:does not|doesn['’]t|do not|don['’]t) contain (?:any )?{_ALLERGENS}"
    rf"|contains no {_ALLERGENS}"
    rf"|no {_ALLERGENS} (?:listed|present|in it)"
    rf"|safe for (?:people|someone|anyone|you|those)?[\w ]{{0,20}}?(?:allerg|intoleran|coeliac|celiac))",
    re.IGNORECASE,
)
_ALLERGY_TOPIC = re.compile(rf"\b{_ALLERGENS}\b|allerg|intoleran|coeliac|celiac|may contain|traces", re.IGNORECASE)

ERR_MSG_ALLERGEN_CLAIM = (
    "Error: your final answer says a product is free of an allergen or safe for an allergy "
    "(\"{claim}\"). The data can be incomplete, so never claim that. Report only what "
    "\"contains\" and \"may_contain\" list, tell the user to check the package label, and "
    "give the final answer again."
)


def allergen_free_claim(final_answer: str) -> str | None:
    """Returns the first "free of / safe for" allergen claim in the answer, if any."""
    match = _ALLERGEN_FREE_CLAIM.search(final_answer)
    return match.group(0) if match else None


def check_final(final_answer: str, history: list, user_query: str) -> str | None:
    """
    Returns an error observation if the final answer uses numbers that aren't in any
    tool result or claims a product is allergen-free; otherwise None.
    """
    claim = allergen_free_claim(final_answer)
    if claim:
        return ERR_MSG_ALLERGEN_CLAIM.format(claim=claim)

    sources = [user_query] + [
        str(h.get("observation", "")) for h in history
        if not str(h.get("observation", "")).startswith("Error:")  # skip our own error messages
    ]
    missing = ungrounded_numbers(final_answer, sources)
    if not missing:
        return None
    shown = ", ".join(f"{x:g}" for x in missing[:5])
    return ERR_MSG_UNGROUNDED.format(numbers=shown)


def closing_notes(user_query: str, final_answer: str, allergy_warnings: list | None = None,
                  profile_has_allergies: bool = False) -> str:
    """
    Fixed notes added to every answer: warnings from the profile allergy check, the
    disclaimer, and the allergen note when the question, answer or profile is about allergies.
    """
    notes = ""
    if allergy_warnings:
        notes += "\n\nAllergy check against your profile:\n" + "\n".join(f"- {w}" for w in allergy_warnings)
    notes += DISCLAIMER
    if profile_has_allergies or _ALLERGY_TOPIC.search(user_query) or _ALLERGY_TOPIC.search(final_answer):
        notes += ALLERGEN_NOTE
    return notes
