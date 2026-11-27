"""
Validates model-generated tool arguments against each function's pydantic model.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass
class ValidationResult:
    """Outcome of validating one set of arguments."""
    ok: bool
    args: Any = None
    message: str = ""
    error_types: list = field(default_factory=list)


def _format_errors(err: ValidationError) -> tuple[str, list]:
    """Turns a pydantic error into a short message the model can act on."""
    messages, types = [], []
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", ())) or "action_input"
        error_type = e.get("type", "")
        types.append(error_type)
        if error_type == "missing":
            messages.append(f"missing required field '{loc}'")
        elif error_type == "extra_forbidden":
            messages.append(f"unknown field '{loc}'")
        else:
            messages.append(f"'{loc}': {e.get('msg', 'invalid value')}")
    return "; ".join(messages), types


def validate_arguments(model: type[BaseModel], action_input) -> ValidationResult:
    """Validates arguments; on success `args` holds every field, defaults included."""
    if isinstance(action_input, str):
        try:
            action_input = json.loads(action_input) if action_input.strip() else {}
        except json.JSONDecodeError:
            return ValidationResult(False, None, "action_input must be a JSON object", ["not_object"])
    if not isinstance(action_input, dict):
        return ValidationResult(False, None, "action_input must be a JSON object", ["not_object"])

    # Models often send null or "" for optional fields they skip.
    action_input = {k: v for k, v in action_input.items() if v is not None and v != ""}

    try:
        validated = model.model_validate(action_input)
    except ValidationError as err:
        message, types = _format_errors(err)
        return ValidationResult(False, action_input, message, types)
    return ValidationResult(True, validated.model_dump())


def _normalize(value):
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if v is not None and v != ""}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def call_key(action: str, args: dict) -> str:
    """A stable string for spotting repeated calls (case and whitespace ignored)."""
    return f"{action}:{json.dumps(_normalize(args), sort_keys=True, default=str)}"
