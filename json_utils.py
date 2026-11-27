"""
Utilities for extracting and parsing JSON from text outputs.
"""

import json
import re
from typing import Any, Dict, Optional, Tuple, Union

_decoder = json.JSONDecoder()


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
        step = _as_step(json.loads(text))
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


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts and parses a JSON object from text, returning None if parsing fails.

    Looks in the text as-is and in a sanitized version (fences, comments and trailing
    commas removed, missing braces added), anywhere in the text, since models often write
    text before the JSON or keep generating after it. An object with step keys ("thought",
    "action", "final_answer") wins over any other object, such as a nested action_input.
    """
    fallback = None
    for candidate in (_strip_fences(text), sanitize_json(text)):
        for obj in _objects(candidate):
            if any(k in obj for k in _STEP_KEYS):
                return obj
            fallback = fallback or obj
    return fallback


def sanitize_model_response(response: str) -> Tuple[bool, Union[Dict[str, Any], str]]:
    """
    Parses a response into JSON, returning a success flag and either the parsed object or an error message.
    """
    json_response = extract_json(response)
    if json_response is None:
        return (False, "Invalid Json")
    return (True, json_response)
