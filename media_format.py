# media_format.py

"""
Formatting helpers for media descriptions and message text.

Conventions (agreed):
- User text is wrapped in French quotes: « … »
- Generated media descriptions are wrapped in single angle quotes: ‹ … ›
- Stickers include set/name plus a description in ‹…›.
"""

FRENCH_OPEN = "«"
FRENCH_CLOSE = "»"

ANGLE_OPEN = "‹"
ANGLE_CLOSE = "›"


def format_media_description(description: str | None) -> str:
    """
    Returns a clause beginning with 'that ...'.
    If there's no usable description (unsupported/unknown), avoid angle quotes.
    """
    s = (description or "").strip()
    if (
        not s
        or s.lower().startswith("not understood")
        or s.lower().startswith("sticker not understood")
    ):
        return "that is not understood"
    return f"that appears as ‹{s}›"


def format_sticker_sentence(
    sticker_name: str, sticker_set: str, description: str
) -> str:
    """
    Full sticker sentence:
      the sticker `<name>` from the sticker set `<set>` that appears as ‹…›
    Falls back to 'that is not understood' when description is missing/unsupported.
    """
    base = f"the sticker `{sticker_name}` from the sticker set `{sticker_set}`"
    s = (description or "").strip()
    return f"{base} {format_media_description(s)}"
