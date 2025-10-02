# media_format.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Formatting helpers for media descriptions and message text.

Conventions (agreed):
- Generated media descriptions are wrapped in single angle quotes: ‹ … ›
- Stickers include set/name plus a description in ‹…›.
"""

ANGLE_OPEN = "‹"
ANGLE_CLOSE = "›"


def format_media_description(description: str | None) -> str:
    """
    Returns a clause beginning with 'that ...'.
    If there's no usable description, return a generic fallback.
    """
    s = (description or "").strip()
    if not s:
        return "that is not understood"
    return f"that appears as {s}"


def format_sticker_sentence(
    sticker_name: str, sticker_set_name: str, description: str | None
) -> str:
    """
    Full sticker sentence:
      the sticker `<name>` from the sticker set `<set>` that appears as ‹…›
    Falls back to 'that is not understood' when description is missing/unsupported.
    """
    base = f"the sticker `{sticker_name}` from the sticker set `{sticker_set_name}`"
    s = (description or "").strip()
    return f"[media] {ANGLE_OPEN}{base} {format_media_description(s)}{ANGLE_CLOSE}"


def format_media_sentence(kind: str, description: str | None) -> str:
    """
    Format a general media sentence with angle quotes:
      ‹the <kind> that appears as <description>›
    Falls back to 'that is not understood' when description is missing.
    """
    media_desc = format_media_description(description)
    return f"[media] {ANGLE_OPEN}the {kind} {media_desc}{ANGLE_CLOSE}"
