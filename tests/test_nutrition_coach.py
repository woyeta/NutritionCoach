import json

import nutrition_coach as nc
from tool_schema import validate_arguments


def run(action, **args):
    validation = validate_arguments(nc.FUNCTIONS[action][0], args)
    assert validation.ok, validation.message
    ok, observation = nc.run(action, validation.args)
    return ok, json.loads(observation) if ok else observation


def test_search_products_normalizes_open_food_facts(fake_http):
    ok, obs = run("search_products", query="nutella")
    product = obs["results"][0]
    assert ok and product["name"] == "Nutella" and product["nutri_score"] == "E"
    assert product["per_100g"]["sugars_g"] == 56.3
    assert product["per_100g"]["sodium_mg"] == 42.8
    assert "search.pl" in fake_http[0]


def test_open_food_facts_vitamins_nova_and_allergens(fake_http):
    ok, obs = run("search_products", query="nutella")
    product = obs["results"][0]
    assert product["per_100g"]["calcium_mg"] == 108.0
    assert product["per_100g"]["iron_mg"] == 4.4
    assert product["per_100g"]["vitamin_d_ug"] == 2.5
    assert "potassium_mg" not in product["per_100g"]  # unreadable value is skipped, not zero
    assert product["nova_group"] == 4 and product["nova_label"] == "ultra-processed food"
    assert product["allergens"]["contains"] == ["milk", "nuts", "soybeans"]
    assert product["allergens"]["may_contain"] == ["gluten"]


def test_barcode_uses_product_endpoint(fake_http):
    ok, obs = run("search_products", query="3017620422003")
    assert ok and obs["results"][0]["barcode"] == "3017620422003"
    assert "/api/v2/product/3017620422003" in fake_http[0]


def test_search_foods_normalizes_usda(fake_http):
    ok, obs = run("search_foods", query="banana")
    assert ok and obs["results"][0]["per_100g"] == {
        "energy_kcal": 89.0, "protein_g": 1.1, "sugars_g": 12.2, "sodium_mg": 1.0,
        "potassium_mg": 358.0, "vitamin_c_mg": 8.7}  # vitamin D in IU is skipped


def test_network_error_is_a_failed_call(monkeypatch):
    def boom(*a, **k):
        raise nc.requests.ConnectionError("offline")
    monkeypatch.setattr(nc.requests, "get", boom)
    ok, obs = run("search_foods", query="banana")
    assert not ok and "request failed" in obs


def test_daily_needs():
    ok, obs = run("calculate_daily_needs", sex="male", age=30, weight_kg=70,
                  height_cm=175, activity="moderate")
    assert obs["bmr_kcal"] == 1649            # 700 + 1093.75 - 150 + 5
    assert obs["maintenance_kcal"] == 2556    # × 1.55
    assert obs["target_kcal"] == 2556         # goal defaults to maintain
    assert obs["free_sugars_max_g"] == 64 and obs["sodium_max_mg"] == 2000


def test_daily_needs_never_goes_below_floor():
    ok, obs = run("calculate_daily_needs", sex="female", age=60, weight_kg=45,
                  height_cm=150, activity="sedentary", goal="lose")
    assert obs["target_kcal"] == 1200 and obs["minimum_floor_applied"]


def test_portion():
    ok, obs = run("scale_to_portion", per_100g={"sugars_g": 56.3, "energy_kcal": 539}, grams=15)
    assert obs["portion"] == {"sugars_g": 8.4, "energy_kcal": 80.8}


def test_results_say_which_food_they_are_for(fake_http):
    ok, obs = run("scale_to_portion", per_100g={"sugars_g": 11}, grams=330, food="Coca-Cola")
    assert obs["food"] == "Coca-Cola" and obs["portion"] == {"sugars_g": 36.3}
    ok, obs = run("scale_to_portion", per_100g={"sugars_g": 11}, grams=330)
    assert obs["food"] == "(not named)"
    ok, obs = run("search_foods", query="banana")
    assert obs["query"] == "banana"


def test_schemas_reject_minors_and_unknown_fields():
    minor = dict(sex="female", age=15, weight_kg=50, height_cm=160, activity="light")
    assert not validate_arguments(nc.DailyNeedsArgs, minor).ok
    bad = validate_arguments(nc.SearchArgs, {"query": "egg", "brand": "x"})
    assert not bad.ok and "unknown field 'brand'" in bad.message
    missing = validate_arguments(nc.SearchArgs, {})
    assert "missing required field 'query'" in missing.message


def test_grounding():
    obs = ['{"per_100g": {"sugars_g": 56.3, "energy_kcal": 539}}']
    assert nc.ungrounded_numbers("Nutella has 56.3 g sugar and 539 kcal per 100 g.", obs) == []
    assert nc.ungrounded_numbers("About 56 g of sugar in 2 products.", obs) == []  # rounding, small counts
    assert nc.ungrounded_numbers("A 15 g serving has 8.4 g sugar.", obs) == [15.0, 8.4]
    assert nc.ungrounded_numbers("Your target is 1,849 kcal.", ['{"target_kcal": 1849}']) == []


def test_check_final_ignores_error_messages():
    history = [{"action": "", "action_input": "", "observation": "Error: ... numbers: 9.9"}]
    assert nc.check_final("It has 9.9 g sugar.", history, "q") is not None


def test_busy_search_is_retried(monkeypatch):
    from conftest import FakeResponse, OFF_PRODUCT
    responses = iter([FakeResponse({}, 503), FakeResponse({"products": [OFF_PRODUCT]})])
    monkeypatch.setattr(nc.requests, "get", lambda url, **kw: next(responses))
    ok, obs = run("search_products", query="nutella")
    assert ok and obs["results"][0]["source"] == "Open Food Facts"


def test_falls_back_to_usda_when_open_food_facts_stays_busy(monkeypatch):
    from conftest import FakeResponse
    branded = {"foods": [{"description": "HAZELNUT SPREAD", "fdcId": 1, "brandOwner": "Ferrero",
                          "gtinUpc": "009800800056", "foodNutrients": [
                              {"nutrientName": "Total Sugars", "unitName": "G", "value": 56.8}]}]}
    calls = []

    def get(url, params=None, **kw):
        calls.append(url)
        return FakeResponse(branded) if "nal.usda.gov" in url else FakeResponse({}, 503)

    monkeypatch.setattr(nc.requests, "get", get)
    ok, obs = run("search_products", query="nutella")
    result = obs["results"][0]
    assert ok and result["source"] == "USDA FoodData Central" and result["brand"] == "Ferrero"
    assert result["per_100g"] == {"sugars_g": 56.8}
    assert "Not provided" in result["allergens"]
    assert sum("openfoodfacts" in u for u in calls) == nc.RETRIES + 1


def test_results_are_cached(fake_http):
    run("search_foods", query="banana")
    run("search_foods", query="banana")
    assert len(fake_http) == 1


def test_allergen_free_claims_are_caught():
    for text in ["This spread is nut-free.", "It is free from gluten.", "It doesn't contain any milk.",
                 "Contains no peanuts.", "It's safe for people with nut allergies.", "No allergens listed."]:
        assert nc.allergen_free_claim(text), text
    for text in ["It contains milk, nuts and soybeans. Check the label.",
                 "Nutella has 56.3 g of sugar per 100 g.", "It may contain traces of gluten."]:
        assert nc.allergen_free_claim(text) is None, text
    assert "never claim" in nc.check_final("It is gluten-free.", [], "q")


def test_closing_notes_add_allergen_note_only_when_relevant():
    assert nc.ALLERGEN_NOTE in nc.closing_notes("Does Nutella contain nuts?", "It contains nuts.")
    assert nc.ALLERGEN_NOTE in nc.closing_notes("Is this OK for my allergy?", "See the label.")
    plain = nc.closing_notes("How much sugar is in Nutella?", "56.3 g per 100 g.")
    assert plain == nc.DISCLAIMER  # "Nutella" and "nutrition" don't count as "nut"


def test_percent_of_limit():
    ok, obs = run("percent_of_limit", amount=3.8, limit=23, label="saturated fat in 40 g of Nutella")
    assert obs["percent_of_limit"] == 16.5 and obs["label"].startswith("saturated fat")
    assert not validate_arguments(nc.PercentArgs, {"amount": 1, "limit": 0}).ok
