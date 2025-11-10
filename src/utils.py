# utils.py
#
# Shared helper functions.
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


def format_username(entity):
    """Return a leading-@ username for a Telegram entity when available."""
    if entity is None:
        return None

    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"

    usernames = getattr(entity, "usernames", None)
    if usernames:
        for handle in usernames:
            handle_value = getattr(handle, "username", None)
            if handle_value:
                return f"@{handle_value}"
    return None


__all__ = ("coerce_to_int", "coerce_to_str", "format_username")
