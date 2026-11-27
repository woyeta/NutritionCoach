"""
Flask app: serves the web page and the /api/ask endpoint.
"""

import logging
import os

from flask import Flask, jsonify, render_template, request

from nutrition_coach import closing_notes
from prompts import DEFAULT_MAX_TURNS, MAX_TURNS_LIMIT, MODEL_ID
from react_engine import react_loop

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "model": MODEL_ID})


@app.route("/api/ask", methods=["POST"])
def ask():
    """Runs the agent on a question: {"query": "...", "max_turns": 8}."""
    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip() if isinstance(data.get("query"), str) else ""
    max_turns = data.get("max_turns", DEFAULT_MAX_TURNS)

    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= MAX_TURNS_LIMIT:
        return jsonify({"success": False, "error": f"max_turns must be an integer from 1 to {MAX_TURNS_LIMIT}"}), 400

    try:
        result = react_loop(query, max_turns=max_turns)
    except Exception as e:
        logger.exception("ReAct loop failed")
        return jsonify({"success": False, "error": str(e)}), 500

    if result["final_answer"]:
        result["final_answer"] += closing_notes(query, result["final_answer"])
        result["steps"][-1]["final_answer"] = result["final_answer"]
    return jsonify({"success": True, "result": result})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
