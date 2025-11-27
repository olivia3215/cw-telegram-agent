# utils/type_coercion.py
#
# Type coercion utilities.

from __future__ import annotations

import json


def coerce_to_int(value):
    """Convert value to int if possible; return None when conversion fails."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def coerce_to_str(value) -> str:
    """Convert value to a string representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)

