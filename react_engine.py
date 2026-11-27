"""
The ReAct loop: the model thinks, calls a Nutrition Coach function, reads the result,
and repeats until it gives a final answer.

Every step is checked before anything runs: the output must be JSON, the function must
exist, and the arguments must pass the function's schema. Rejected steps are fed back
to the model as the observation so it can fix them. Guards stop the run on repeated
identical calls, too many consecutive errors, or too many turns. Final answers must
pass nutrition_coach.check_final (no numbers that aren't in a tool result, and no
"allergen-free" claims).
"""

import json
import logging

import nutrition_coach as nc
from agent_format import Generation, history_entry
from json_utils import extract_json
from prompts import (
    DEFAULT_MAX_TURNS,
    ERR_MSG_API_FAIL,
    ERR_MSG_INVALID_ACTION,
    ERR_MSG_INVALID_ARGS,
    ERR_MSG_INVALID_JSON,
    ERR_MSG_NO_ACTION,
    ERR_MSG_REPEATED_CALL,
    MAX_CONSECUTIVE_ERRORS,
    MAX_TURNS_LIMIT,
)
from tool_schema import call_key, validate_arguments

logger = logging.getLogger(__name__)


_NAME_KEYS = ("tool", "tool_name", "function", "function_name", "name")
_ARGS_KEYS = ("arguments", "parameters", "args", "input", "tool_input", "action_input")
_ANSWER_KEYS = ("answer", "response", "final")
_FINAL_ACTIONS = ("final_answer", "finalanswer", "final", "answer", "finish", "respond")


def normalize_step(response: dict) -> dict:
    """
    Maps common alternative shapes from small models onto the expected keys:
    {"name": "search_products", "arguments": {...}}, {"search_products": {...}},
    {"answer": "..."}, and a final answer written as a tool call:
    {"action": "final_answer", "action_input": {"final_answer": "..."}}.
    Anything else is returned unchanged.
    """
    action = response.get("action")
    if isinstance(action, str) and action.strip().lower() in _FINAL_ACTIONS and not response.get("final_answer"):
        action_input = response.get("action_input")
        if isinstance(action_input, str):
            try:
                action_input = json.loads(action_input)
            except json.JSONDecodeError:
                pass
        if isinstance(action_input, dict):
            answer = next((action_input[k] for k in ("final_answer",) + _ANSWER_KEYS + ("text",)
                           if isinstance(action_input.get(k), str) and action_input[k].strip()), None)
        else:
            answer = action_input if isinstance(action_input, str) and action_input.strip() else None
        if answer:
            return {"thought": response.get("thought", ""), "final_answer": answer}
    if response.get("action") or response.get("final_answer"):
        return response
    for name_key in _NAME_KEYS:
        name = response.get(name_key)
        if isinstance(name, str) and name in nc.FUNCTIONS:
            args = next((response[k] for k in _ARGS_KEYS if k in response), {})
            return {**response, "action": name, "action_input": args}
    for name in nc.FUNCTIONS:
        if isinstance(response.get(name), dict):
            return {**response, "action": name, "action_input": response[name]}
    for answer_key in _ANSWER_KEYS:
        answer = response.get(answer_key)
        if isinstance(answer, str) and answer.strip():
            return {**response, "final_answer": answer}
    return response


def _default_generate(user_query: str, history: list) -> Generation:
    from model_loader import run_inference
    return run_inference(user_query, nc.DOCUMENTATION, history)


def react_loop(user_query: str, max_turns: int = DEFAULT_MAX_TURNS, generate=None) -> dict:
    """
    Answers a query. `generate(query, history)` returns a Generation (or str); it
    defaults to the Gemma model and can be replaced in tests.

    Returns {"final_answer", "steps", "turns_used", "status"}, where status is one of
    success, unverified, loop_detected, consecutive_errors, max_turns_reached.
    """
    generate = generate or _default_generate
    max_turns = max(1, min(int(max_turns), MAX_TURNS_LIMIT))
    logger.info(f"Starting ReAct loop for query: {user_query}")

    history, steps = [], []
    consecutive_errors = 0
    last_key, repeat_count = None, 0
    final_rejected = False

    def result(final_answer, turns_used, status):
        return {"final_answer": final_answer, "steps": steps, "turns_used": turns_used, "status": status}

    def reject(turn, fields: dict, observation: str) -> bool:
        """Records a rejected step and feeds it back. Returns True if the run should stop."""
        nonlocal consecutive_errors
        consecutive_errors += 1
        steps.append({"turn": turn, "type": "error", **fields})
        history.append(history_entry(fields.get("action", ""), fields.get("action_input", ""), observation))
        return consecutive_errors >= MAX_CONSECUTIVE_ERRORS

    for turn in range(1, max_turns + 1):
        generation = generate(user_query, history)
        text = generation.text if isinstance(generation, Generation) else str(generation)
        response = extract_json(text)

        if not isinstance(response, dict):
            logger.warning(f"Turn {turn}: model output is not a JSON object: {text!r}")
            fields = {"error": "Invalid JSON response from model", "raw_response": text[:500]}
            if reject(turn, fields, ERR_MSG_INVALID_JSON):
                return result(None, turn, "consecutive_errors")
            continue

        response = normalize_step(response)
        thought = response.get("thought", "")

        # Final answer
        final_answer = response.get("final_answer")
        if final_answer:
            problem = nc.check_final(str(final_answer), history, user_query)
            if problem and not final_rejected:
                final_rejected = True
                if reject(turn, {"thought": thought, "error": f"Final answer rejected: {problem}"}, problem):
                    return result(None, turn, "consecutive_errors")
                continue
            steps.append({"turn": turn, "type": "final_answer", "thought": thought, "final_answer": final_answer})
            return result(final_answer, turn, "unverified" if problem else "success")

        # Tool call
        action = response.get("action")
        if action is not None and not isinstance(action, str):
            action = json.dumps(action)  # e.g. the model sent an object instead of a name
        action_input = response.get("action_input", {})
        fields = {"thought": thought, "action": action or "", "action_input": action_input}

        if not action:
            error, observation = "No action specified", ERR_MSG_NO_ACTION
            fields["raw_response"] = text[:500]
            logger.warning(f"Turn {turn}: no action in model output: {text!r}")
        elif action not in nc.FUNCTIONS:
            error = f"Invalid action: '{action}'"
            fields["raw_response"] = text[:500]
            observation = ERR_MSG_INVALID_ACTION.format(tool_functions_list=", ".join(nc.FUNCTIONS))
        else:
            validation = validate_arguments(nc.FUNCTIONS[action][0], action_input)
            if not validation.ok:
                error = f"Invalid arguments: {validation.message}"
                observation = ERR_MSG_INVALID_ARGS.format(action=action, details=validation.message)
            else:
                key = call_key(action, validation.args)
                if key == last_key:
                    repeat_count += 1
                    steps.append({"turn": turn, "type": "error", **fields, "error": "Repeated identical tool call"})
                    if repeat_count >= 2:
                        return result(None, turn, "loop_detected")
                    history.append(history_entry(action, action_input, ERR_MSG_REPEATED_CALL.format(action=action)))
                    continue

                ok, output = nc.run(action, validation.args)
                last_key, repeat_count = key, 0
                if ok:
                    steps.append({"turn": turn, "type": "thought_action", **fields, "observation": output})
                    history.append(history_entry(action, action_input, output))
                    consecutive_errors = 0
                    continue
                error, observation = f"Tool call failed: {output}", ERR_MSG_API_FAIL.format(api_response=output)

        if reject(turn, {**fields, "error": error}, observation):
            return result(None, turn, "consecutive_errors")

    return result(None, max_turns, "max_turns_reached")
