"""Checks a tool call's arguments against the tool's own schema before anything else happens to the call.

A model often sends "5" for a whole number or leaves out a required field. A bad call must not reach the safety gate (the
user would be asked to approve something that cannot run) or the tool (a half-working action). Plain, safe slips are
repaired (a number or a true/false written as text); anything else comes back as an `Error:` result the model can fix in
one more try. Only the common schema words are understood (type, required, enum); a schema using anything else is skipped.
"""
from __future__ import annotations

import re

_WHOLE = re.compile(r"^[+-]?\d+$")
_TYPES = ("string", "integer", "number", "boolean", "array", "object")


def _fix(kind: str, value):
    """(value, ok): the value as `kind` wants it, repaired when the slip is unambiguous."""
    if kind == "string":
        return value, isinstance(value, str)
    if kind == "boolean":
        if isinstance(value, bool):
            return value, True
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true", True
        return value, False
    if kind in ("integer", "number") and isinstance(value, bool):
        return value, False
    if kind == "integer":
        if isinstance(value, int):
            return value, True
        if isinstance(value, float) and value.is_integer():
            return int(value), True
        if isinstance(value, str) and _WHOLE.match(value.strip()):
            return int(value.strip()), True
        return value, False
    if kind == "number":
        if isinstance(value, (int, float)):
            return value, True
        if isinstance(value, str):
            try:
                return float(value.strip()), True
            except ValueError:
                return value, False
        return value, False
    if kind == "array":
        return value, isinstance(value, list)
    if kind == "object":
        return value, isinstance(value, dict)
    return value, True


def check_args(tool, args) -> tuple[object, str | None]:
    """(arguments to use, None) or (arguments, "Error: ...") for a call that cannot work."""
    name = getattr(tool, "name", "this tool")
    if not isinstance(args, dict):
        return args, f"Error: {name} needs its arguments as an object. Fix that and call it again."
    schema = getattr(tool, "parameters", None)
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return args, None
    problems: list[str] = []
    for key in schema.get("required") or []:
        if key not in args:
            kind = (props.get(key) or {}).get("type") if isinstance(props.get(key), dict) else None
            problems.append(f"'{key}' is required" + (f" (a {kind})" if kind in _TYPES else ""))
    fixed = dict(args)
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue
        kind = spec.get("type")
        if kind in _TYPES:
            value, ok = _fix(kind, value)
            if not ok:
                problems.append(f"'{key}' must be a {kind}, not {type(args[key]).__name__} {str(args[key])[:30]!r}")
                continue
            fixed[key] = value
        options = spec.get("enum")
        if isinstance(options, list) and fixed[key] not in options:
            problems.append(f"'{key}' must be one of {', '.join(map(str, options))}")
    if problems:
        return args, f"Error: {name} was called with bad arguments: {'; '.join(problems)}. Fix that and call it again."
    return fixed, None
