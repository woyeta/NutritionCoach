import json

from react_engine import react_loop
from prompts import MAX_TURNS_LIMIT


def scripted(outputs):
    """A fake model that returns the given outputs in order and records each history."""
    outputs = iter(outputs)
    seen = []

    def generate(query, history):
        seen.append(json.loads(json.dumps(history)))
        return next(outputs)
    generate.seen = seen
    return generate


def call(action, **args):
    return json.dumps({"thought": "t", "action": action, "action_input": args})


def final(text):
    return json.dumps({"thought": "t", "final_answer": text})


SEARCH = call("search_products", query="nutella")


def test_end_to_end(fake_http):
    gen = scripted([
        SEARCH,
        call("scale_to_portion", per_100g={"sugars_g": 56.3}, grams=15),
        final("A 15 g serving of Nutella has 8.4 g of sugar (Nutri-Score E)."),
    ])
    result = react_loop("How much sugar is in 15 g of Nutella?", generate=gen)
    assert result["status"] == "success" and result["turns_used"] == 3
    assert [s["type"] for s in result["steps"]] == ["thought_action", "thought_action", "final_answer"]


def test_made_up_numbers_are_rejected_once(fake_http):
    invented = final("A 15 g serving has 9.9 g of sugar.")
    result = react_loop("Sugar in 15 g of Nutella?", generate=scripted([SEARCH, invented, invented]))
    assert result["steps"][1]["type"] == "error" and "9.9" in result["steps"][1]["error"]
    assert result["status"] == "unverified"  # the second attempt is returned, but flagged


def test_invalid_arguments_are_fed_back_and_fixed(fake_http):
    gen = scripted([call("search_products"), SEARCH, final("Nutella: Nutri-Score E.")])
    result = react_loop("q", generate=gen)
    assert result["status"] == "success"
    assert "missing required field 'query'" in gen.seen[1][0]["observation"]
    assert len(fake_http) == 1  # the invalid call never ran


def test_unknown_function_is_rejected(fake_http):
    gen = scripted([call("deleteEverything"), final("done")])
    result = react_loop("q", generate=gen)
    assert "search_products" in gen.seen[1][0]["observation"]
    assert result["status"] == "success"


def test_object_action_is_rejected_not_crashing():
    bad = json.dumps({"thought": "t", "action": {"name": "search_products"}, "action_input": {}})
    gen = scripted([bad, final("done")])
    result = react_loop("q", generate=gen)
    assert result["status"] == "success" and "Invalid action" in gen.seen[1][0]["observation"]


def test_repeated_call_is_nudged_then_stopped(fake_http):
    gen = scripted([SEARCH, SEARCH, SEARCH])
    result = react_loop("q", generate=gen)
    assert result["status"] == "loop_detected"
    assert "already called" in gen.seen[2][-1]["observation"]


def test_max_turns_is_capped(fake_http):
    outputs = [call("search_products", query=f"food {i}") for i in range(50)]
    result = react_loop("q", max_turns=50, generate=scripted(outputs))
    assert result["status"] == "max_turns_reached" and result["turns_used"] == MAX_TURNS_LIMIT


def test_consecutive_errors_end_the_run():
    result = react_loop("q", generate=scripted(["oops", "still not json", "nope"]))
    assert result["status"] == "consecutive_errors"


def test_tool_failure_is_fed_back(monkeypatch):
    import nutrition_coach as nc

    def boom(*a, **k):
        raise nc.requests.ConnectionError("offline")
    monkeypatch.setattr(nc.requests, "get", boom)
    gen = scripted([SEARCH, final("Sorry, the food database is unavailable.")])
    result = react_loop("q", generate=gen)
    assert "offline" in gen.seen[1][0]["observation"] and result["status"] == "success"


def test_allergen_free_claim_is_rejected_once(fake_http):
    claim = final("Nutella is gluten-free.")
    fixed = final("Nutella lists milk, nuts and soybeans and may contain gluten. Check the label.")
    gen = scripted([SEARCH, claim, fixed])
    result = react_loop("Is Nutella gluten-free?", generate=gen)
    assert "never claim" in gen.seen[2][-1]["observation"]
    assert result["status"] == "success"


def test_alternative_call_shapes_are_accepted(fake_http):
    gen = scripted([
        json.dumps({"name": "search_products", "arguments": {"query": "nutella"}}),
        json.dumps({"scale_to_portion": {"per_100g": {"sugars_g": 56.3}, "grams": 15}}),
        json.dumps({"answer": "A 15 g serving has 8.4 g of sugar."}),
    ])
    result = react_loop("Sugar in 15 g of Nutella?", generate=gen)
    assert result["status"] == "success"
    assert [s.get("action") for s in result["steps"][:2]] == ["search_products", "scale_to_portion"]


def test_no_action_error_shows_model_output():
    gen = scripted([json.dumps({"query": "cola"}), final("done")])
    result = react_loop("q", generate=gen)
    assert result["steps"][0]["error"] == "No action specified"
    assert '"query": "cola"' in result["steps"][0]["raw_response"]


def test_default_allows_a_four_step_question_with_two_retries(fake_http):
    from prompts import DEFAULT_MAX_TURNS
    assert DEFAULT_MAX_TURNS >= 6
