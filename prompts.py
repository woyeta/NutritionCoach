"""
System prompt, error messages and settings.
"""

import os

SYSTEM_PROMPT = """You are an intelligent AI agent that answers questions by reasoning step-by-step.

You have access to the following tools:
{tool_docs}

Use this STRICT format for reasoning — output a JSON block:
{{"thought": "...", "action": "...", "action_input": {{"key": "value"}}}}

If you have enough information, output a final JSON block:
{{"thought": "...", "final_answer": "..."}}

RULES:
- STOP generating immediately after closing the JSON block.
- action_input MUST be a valid JSON object (double-quoted keys, no trailing commas).
- Only call tools listed above; never fabricate tool names or results.
- Always output valid JSON — no trailing commas, no single quotes.
- Use the final_answer JSON format (with \"thought\" and \"final_answer\" keys, omitting \"action\" and \"action_input\") only when you have gathered enough information to fully answer the user's question."""

# Error observations are returned to the model in the history, so they use plain
# braces (they are never passed through str.format unless they have a placeholder).
ERR_MSG_INVALID_JSON = (
    'Error: Invalid JSON response was generated. Output exactly one JSON object and nothing '
    'else (no text before or after it). Use this STRICT format for reasoning '
    '— output a JSON block:\n'
    '{"thought": "...", "action": "...", "action_input": {"key": "value"}}\n\n'
    'If you have enough information, output a final JSON block:\n'
    '{"thought": "...", "final_answer": "..."}'
)

ERR_MSG_NO_ACTION = (
    'Error: No action was found. Use this STRICT format for reasoning '
    '— output a JSON block:\n'
    '{"thought": "...", "action": "...", "action_input": {"key": "value"}}\n\n'
    'If you have enough information, output a final JSON block:\n'
    '{"thought": "...", "final_answer": "..."}'
)

ERR_MSG_INVALID_ACTION = (
    "Error: Invalid action. The action value must exactly match a name from the "
    "following [{tool_functions_list}]"
)

ERR_MSG_INVALID_ARGS = (
    "Error: Invalid action_input for {action}: {details}. Fix the arguments and call the tool again."
)

ERR_MSG_REPEATED_CALL = (
    "Error: {action} was already called with these exact arguments. "
    "Use the previous observation or give the final answer."
)

ERR_MSG_API_FAIL = (
    "Error: API call failed or returned an invalid response. Details: {api_response}"
)

MODEL_ID = os.environ.get("MODEL_ID", "google/gemma-4-E2B-it")  # or google/gemma-4-E4B-it
MAX_NEW_TOKENS = 512
MAX_TURNS_LIMIT = 8
MAX_CONSECUTIVE_ERRORS = 3
