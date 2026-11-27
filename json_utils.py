"""
Utilities for extracting and parsing JSON from text outputs.
"""

import json
import re
from typing import Any, Dict, Optional, Tuple, Union

_decoder = json.JSONDecoder(strict=False)  # allow raw line breaks inside strings (models write them)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _strip_comments(text: str) -> str:
    """Removes // line comments that are outside of JSON strings (URLs stay intact)."""
    out = []
    in_string = escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def sanitize_json(raw: str) -> str:
    """
    Cleans raw text to format it as valid JSON.
    """
    text = _strip_fences(raw)
    text = _strip_comments(text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    if text.startswith("{") and text.count("{") > text.count("}"):
        text += "}" * (text.count("{") - text.count("}"))
    return text.strip()


_STEP_KEYS = ("action", "final_answer", "thought")


def _as_step(value) -> Optional[Dict[str, Any]]:
    """A step object, or the first one in a list (models sometimes wrap the call in [...])."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((v for v in value if isinstance(v, dict) and any(k in v for k in _STEP_KEYS)), None)
    return None


def _objects(text: str):
    """Yields every JSON object (or step in a list) that starts at a '{' or '[' in the text."""
    try:
        step = _as_step(_decoder.decode(text))
        if step is not None:
            yield step
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                value, _ = _decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            step = _as_step(value)
            if step is not None:
                yield step


def _string_at(text: str, i: int) -> Optional[str]:
    try:
        value, _ = _decoder.raw_decode(text, i)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) else None


def _repair_step(text: str) -> Optional[Dict[str, Any]]:
    """
    Reads "thought", "action", "action_input" and "final_answer" one at a time, for
    outputs that are almost JSON (e.g. a stray quote before the closing brace).
    """
    step = {}
    for key in ("thought", "action", "final_answer"):
        match = re.search(rf'"{key}"\s*:\s*(?=")', text)
        if match:
            value = _string_at(text, match.end())
            if value is not None:
                step[key] = value
    match = re.search(r'"action_input"\s*:\s*(?=\{)', text)
    if match:
        try:
            value, _ = _decoder.raw_decode(text, match.end())
            if isinstance(value, dict):
                step["action_input"] = value
        except json.JSONDecodeError:
            pass
    return step if ("action" in step or "final_answer" in step) else None


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts and parses a JSON object from text, returning None if parsing fails.

    Looks in the text as-is and in a sanitized version (fences, comments and trailing
    commas removed, missing braces added), anywhere in the text, since models often write
    text before the JSON or keep generating after it. An object with step keys ("thought",
    "action", "final_answer") wins over any other object, such as a nested action_input.
    If no complete step object is found, the step's fields are read one by one.
    """
    fallback = None
    for candidate in (_strip_fences(text), sanitize_json(text)):
        for obj in _objects(candidate):
            if any(k in obj for k in _STEP_KEYS):
                return obj
            fallback = fallback or obj
    return _repair_step(_strip_fences(text)) or fallback


def sanitize_model_response(response: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """
    Parses a response into JSON, returning a success flag and either the parsed object or an error message.
    """
    json_response = extract_json(response)
    if json_response is None:
        return (False, "Invalid Json")
    return (True, json_response)
