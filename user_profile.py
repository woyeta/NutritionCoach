"""
The user's saved profile (biodata): sex, age, height, weight, activity, goal, diet,
allergies and health conditions.

The profile is stored in the user's browser and sent with each question. The server
validates it, adds it to the model's input so personal questions use it automatically,
and checks product allergens against it. The server never saves it.
"""

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tool_schema import _format_errors


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sex: Optional[Literal["male", "female"]] = None
    age: Optional[int] = Field(None, ge=1, le=120)
    height_cm: Optional[float] = Field(None, gt=50, lt=260)
    weight_kg: Optional[float] = Field(None, gt=10, lt=400)
    activity: Optional[Literal["sedentary", "light", "moderate", "active", "very_active"]] = None
    goal: Optional[Literal["lose", "maintain", "gain"]] = None
    diet: Optional[str] = Field(None, max_length=60)
    allergies: Optional[str] = Field(None, max_length=200)
    conditions: Optional[str] = Field(None, max_length=200)

    def is_empty(self) -> bool:
        return not any(v not in (None, "") for v in self.model_dump().values())


def parse_profile(data) -> tuple[Optional[Profile], Optional[str]]:
    """Returns (profile, None), (None, None) for no profile, or (None, error message)."""
    if not data:
        return None, None
    if not isinstance(data, dict):
        return None, "profile must be an object"
    cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    cleaned = {k: v for k, v in cleaned.items() if v not in (None, "")}
    try:
        profile = Profile.model_validate(cleaned)
    except ValidationError as err:
        return None, f"Invalid profile: {_format_errors(err)[0]}"
    return (None, None) if profile.is_empty() else (profile, None)


def _num(x: float) -> str:
    return f"{x:g}"


def profile_text(profile: Optional[Profile]) -> str:
    """One line for the model, e.g. "User profile (...): sex: female, age: 30, ..."."""
    if profile is None:
        return ""
    parts = []
    if profile.sex:
        parts.append(f"sex: {profile.sex}")
    if profile.age is not None:
        parts.append(f"age: {profile.age}")
    if profile.height_cm is not None:
        parts.append(f"height_cm: {_num(profile.height_cm)}")
    if profile.weight_kg is not None:
        parts.append(f"weight_kg: {_num(profile.weight_kg)}")
    if profile.activity:
        parts.append(f"activity: {profile.activity}")
    if profile.goal:
        parts.append(f"goal: {profile.goal}")
    if profile.diet:
        parts.append(f"diet: {profile.diet}")
    if profile.allergies:
        parts.append(f"allergies: {profile.allergies}")
    if profile.conditions:
        parts.append(f"health conditions: {profile.conditions}")
    if not parts:
        return ""
    return ("User profile (saved by the user; use it for personal questions unless the "
            "question gives other values): " + ", ".join(parts))


def with_profile(user_query: str, profile: Optional[Profile]) -> str:
    """The query as the model sees it: the question plus the profile line, if any."""
    text = profile_text(profile)
    return f"{user_query}\n{text}" if text else user_query


# ── Allergy check against product results ──

# User's words → the names Open Food Facts uses (after "en:" is removed).
_SYNONYMS = {
    "dairy": "milk", "lactose": "milk", "milk": "milk",
    "peanut": "peanuts", "peanuts": "peanuts", "groundnut": "peanuts", "groundnuts": "peanuts",
    "nut": "nuts", "nuts": "nuts", "tree nut": "nuts", "tree nuts": "nuts", "hazelnut": "nuts",
    "hazelnuts": "nuts", "almond": "nuts", "almonds": "nuts", "cashew": "nuts", "cashews": "nuts",
    "egg": "eggs", "eggs": "eggs",
    "soy": "soybeans", "soya": "soybeans", "soybean": "soybeans", "soybeans": "soybeans",
    "gluten": "gluten", "wheat": "gluten", "coeliac": "gluten", "celiac": "gluten",
    "sesame": "sesame seeds", "sesame seeds": "sesame seeds",
    "fish": "fish", "shellfish": "crustaceans", "crustacean": "crustaceans",
    "crustaceans": "crustaceans", "shrimp": "crustaceans", "prawn": "crustaceans",
    "mollusc": "molluscs", "molluscs": "molluscs", "mustard": "mustard", "celery": "celery",
    "lupin": "lupin", "sulphite": "sulphur dioxide and sulphites",
    "sulphites": "sulphur dioxide and sulphites", "sulfites": "sulphur dioxide and sulphites",
}


def _canonical(term: str) -> str:
    term = term.strip().lower()
    return _SYNONYMS.get(term, term)


def user_allergens(profile: Optional[Profile]) -> list[str]:
    if profile is None or not profile.allergies:
        return []
    terms = re.split(r",|;|/|\band\b|\n", profile.allergies.lower())
    return sorted({_canonical(t) for t in terms if t.strip()} - {"none", "no", "nil", "n/a"})


def allergy_warnings(profile: Optional[Profile], steps: list) -> list[str]:
    """
    Fixed warnings for products the agent looked up whose label lists one of the user's
    allergens, plus a note for products with no allergen data.
    """
    mine = user_allergens(profile)
    if not mine:
        return []
    warnings = []
    for step in steps:
        if step.get("action") != "search_products" or not step.get("observation"):
            continue
        try:
            results = json.loads(step["observation"]).get("results", [])
        except (json.JSONDecodeError, AttributeError):
            continue
        for product in results:
            name = product.get("name", "A product")
            allergens = product.get("allergens")
            if not isinstance(allergens, dict):
                warnings.append(f"No allergen data for {name}; check its label.")
                continue
            contains = {_canonical(a) for a in allergens.get("contains", [])}
            may_contain = {_canonical(a) for a in allergens.get("may_contain", [])}
            for allergen in mine:  # "contains" first, then "may contain"
                if allergen in contains:
                    warnings.append(f"{name} lists {allergen}, which is in your allergy list.")
            for allergen in mine:
                if allergen in may_contain and allergen not in contains:
                    warnings.append(f"{name} may contain {allergen}, which is in your allergy list.")
    return list(dict.fromkeys(warnings))[:8]  # dedupe, keep order
