import json

import react_engine
from app import app
from react_engine import react_loop
from user_profile import allergy_warnings, parse_profile, profile_text, user_allergens, with_profile

ME = {"sex": "female", "age": 30, "height_cm": 165, "weight_kg": 62, "activity": "moderate",
      "goal": "lose", "diet": "vegetarian", "allergies": "Dairy, peanuts", "conditions": ""}


def test_parse_profile():
    profile, error = parse_profile(ME)
    assert error is None and profile.weight_kg == 62 and profile.conditions is None
    assert parse_profile(None) == (None, None)
    assert parse_profile({"age": "", "sex": None}) == (None, None)  # all empty = no profile
    assert parse_profile({"sex": "female", "unknown": 1})[0].sex == "female"  # extra keys ignored
    assert "age" in parse_profile({"age": 200})[1]
    assert parse_profile({"sex": "other"})[1].startswith("Invalid profile")


def test_profile_text_goes_into_the_model_query():
    profile, _ = parse_profile(ME)
    text = profile_text(profile)
    assert "weight_kg: 62" in text and "height_cm: 165" in text and "allergies: Dairy, peanuts" in text
    assert "health conditions" not in text  # empty fields are left out
    assert with_profile("Hi", profile) == "Hi\n" + text
    assert with_profile("Hi", None) == "Hi"


def test_user_allergens_are_normalized():
    profile, _ = parse_profile({"allergies": "Dairy, peanuts and wheat; none"})
    assert user_allergens(profile) == ["gluten", "milk", "peanuts"]


def test_agent_uses_the_profile_and_its_numbers_are_grounded():
    profile, _ = parse_profile(ME)
    seen = []
    outputs = iter([
        json.dumps({"thought": "Use the profile.", "action": "calculate_daily_needs", "action_input": {
            "sex": "female", "age": 30, "weight_kg": 62, "height_cm": 165, "activity": "moderate", "goal": "lose"}}),
        json.dumps({"thought": "t", "final_answer": "At 62 kg and 165 cm, your target is 1,577 kcal a day."}),
    ])

    def generate(query, history):
        seen.append(query)
        return next(outputs)

    result = react_loop("What should my daily calories be?", generate=generate, profile=profile)
    assert result["status"] == "success"  # 62 and 165 come from the profile, 1,577 from the tool
    assert "User profile" in seen[0] and "weight_kg: 62" in seen[0]


def product_step(products):
    return {"action": "search_products", "observation": json.dumps({"query": "x", "results": products})}


def test_allergy_warnings():
    profile, _ = parse_profile({"allergies": "dairy, wheat"})
    steps = [product_step([
        {"name": "Nutella", "allergens": {"contains": ["milk", "nuts"], "may_contain": ["gluten"]}},
        {"name": "HAZELNUT SPREAD", "allergens": "Not provided by this source."},
    ])]
    assert allergy_warnings(profile, steps) == [
        "Nutella lists milk, which is in your allergy list.",
        "Nutella may contain gluten, which is in your allergy list.",
        "No allergen data for HAZELNUT SPREAD; check its label.",
    ]
    assert allergy_warnings(None, steps) == []
    assert allergy_warnings(parse_profile({"allergies": "fish"})[0], steps) == [
        "No allergen data for HAZELNUT SPREAD; check its label."]


def test_ask_with_profile(fake_http, monkeypatch):
    outputs = iter([
        json.dumps({"thought": "t", "action": "search_products", "action_input": {"query": "nutella"}}),
        json.dumps({"thought": "t", "final_answer": "Nutella lists milk and nuts. Check the label."}),
    ])
    monkeypatch.setattr(react_engine, "_default_generate", lambda q, h: next(outputs))
    body = app.test_client().post("/api/ask", json={"query": "Is Nutella OK for me?", "profile": ME}).get_json()
    answer = body["result"]["final_answer"]
    assert body["success"] and body["result"]["used_profile"]
    assert "Allergy check against your profile:\n- Nutella lists milk" in answer
    assert "Always check the package label" in answer


def test_ask_rejects_an_invalid_profile():
    response = app.test_client().post("/api/ask", json={"query": "q", "profile": {"age": 500}})
    assert response.status_code == 400 and "Invalid profile" in response.get_json()["error"]
