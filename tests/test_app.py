import json

import react_engine
from app import app
from nutrition_coach import DISCLAIMER


def test_ask_returns_answer_with_disclaimer(fake_http, monkeypatch):
    outputs = iter([
        json.dumps({"thought": "t", "action": "search_products", "action_input": {"query": "nutella"}}),
        json.dumps({"thought": "t", "final_answer": "Nutella has 56.3 g sugar per 100 g."}),
    ])
    monkeypatch.setattr(react_engine, "_default_generate", lambda q, h: next(outputs))

    body = app.test_client().post("/api/ask", json={"query": "Sugar in Nutella?"}).get_json()
    assert body["success"] and body["result"]["status"] == "success"
    assert body["result"]["final_answer"].endswith(DISCLAIMER)
    assert body["result"]["steps"][-1]["final_answer"] == body["result"]["final_answer"]


def test_ask_validates_input():
    client = app.test_client()
    assert client.post("/api/ask", json={}).status_code == 400
    assert client.post("/api/ask", json={"query": "q", "max_turns": 99}).status_code == 400


def test_index_and_health():
    client = app.test_client()
    assert b"Nutrition Coach" in client.get("/").data
    assert client.get("/api/health").get_json()["status"] == "ok"
