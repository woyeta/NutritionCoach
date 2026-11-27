"""
Builds the chat messages for one reasoning step.

Each step is a system message (instructions plus tool docs) and a user message of the
form "Query: <query>\nHistory: <json list>", where each history entry has the keys
"action", "action_input" (a JSON string) and "observation".
"""

import json
from dataclasses import dataclass

from prompts import SYSTEM_PROMPT


@dataclass
class Generation:
    """A single model completion."""
    text: str
    n_tokens: int = 0
    latency_ms: float = 0.0


def history_entry(action: str, action_input, observation: str) -> dict:
    """Creates a history entry."""
    if not isinstance(action_input, str):
        action_input = json.dumps(action_input)
    return {"action": action, "action_input": action_input, "observation": observation}


def build_messages(tool_docs: str, query: str, history: list) -> list:
    """Creates chat messages for one reasoning step."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(tool_docs=tool_docs.strip())},
        {"role": "user", "content": f"Query: {query.strip()}\nHistory: {json.dumps(history)}"},
    ]
