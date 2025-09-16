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

def format_media_description(description: str) -> str:
    """Wrap a generated description in single angle quotes."""
    return f"{ANGLE_OPEN}{description}{ANGLE_CLOSE}"

def format_sticker_sentence(sticker_name: str, sticker_set: str, description: str) -> str:
    """
    Format the sticker mention + description, e.g.:
    the sticker '😀' from the sticker set 'WENDYAI' that appears as ‹a picture of …›
    """
    return (
        f"the sticker '{sticker_name}' from the sticker set '{sticker_set}' "
        f"that appears as {format_media_description(description)}"
    )
