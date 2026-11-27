from json_utils import extract_json


def test_urls_inside_strings_survive():
    text = '{"action": "get", "action_input": {"url": "https://httpbin.org/get"}}'
    assert extract_json(text)["action_input"]["url"] == "https://httpbin.org/get"


def test_comments_outside_strings_are_removed():
    text = '{\n  "action": "get", // pick the tool\n  "action_input": {}\n}'
    assert extract_json(text) == {"action": "get", "action_input": {}}


def test_trailing_text_after_object():
    text = '{"thought": "t", "final_answer": "a"}\nObservation: {"fake": true}'
    assert extract_json(text) == {"thought": "t", "final_answer": "a"}


def test_fences_and_trailing_commas():
    assert extract_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}


def test_missing_closing_brace():
    assert extract_json('{"action": "get", "action_input": {"q": "x"}') == {"action": "get", "action_input": {"q": "x"}}


def test_not_json():
    assert extract_json("no json here") is None


def test_text_with_braces_before_the_json():
    text = 'I will call search_products with {query} next.\n{"thought": "t", "action": "search_products", "action_input": {"query": "cola"}}'
    assert extract_json(text)["action"] == "search_products"


def test_call_wrapped_in_a_list():
    text = '[{"thought": "t", "action": "search_products", "action_input": {"query": "cola"}}]'
    assert extract_json(text)["action"] == "search_products"


def test_prose_answer_is_not_json():
    assert extract_json("The daily limit is 67 g, so a can is about half of it.") is None


# Real Gemma 4 E2B outputs from a Colab run.

def test_raw_line_breaks_inside_strings():
    text = '```json\n{"thought": "First line.\nSecond line.", "action": "search_products", "action_input": {"query": "Coca-Cola"}}\n```'
    step = extract_json(text)
    assert step["action"] == "search_products" and step["action_input"] == {"query": "Coca-Cola"}


def test_stray_quote_before_closing_brace():
    text = '```json\n{"thought": "Search for it.\n\nStep 2: search.", "action": "search_products", "action_input": {"query": "Coca-Cola"}"}\n```'
    step = extract_json(text)
    assert step["action"] == "search_products" and step["action_input"] == {"query": "Coca-Cola"}
    assert step["thought"].startswith("Search for it.")


def test_cut_off_output_keeps_the_action():
    text = '{"thought": "Scale it.", "action": "scale_to_portion", "action_input": {"per_100g": {"sugars_g": 10.'
    step = extract_json(text)
    assert step["action"] == "scale_to_portion" and "action_input" not in step
