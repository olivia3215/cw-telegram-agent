# media_format.py

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
    If there's no usable description (unsupported/unknown), avoid angle quotes.
    """
    s = (description or "").strip()
    if (
        not s
        or s.lower().startswith("not understood")
        or s.lower().startswith("sticker not understood")
    ):
        return "that is not understood"
    return f"that appears as {s}"


def format_media_description_from_cache(cache_record: dict | None) -> str:
    """
    Returns a clause beginning with 'that ...' based on cache record.
    Uses failure_reason field if description is not available.
    """
    if not isinstance(cache_record, dict):
        return "that is not understood"

    description = cache_record.get("description")
    failure_reason = cache_record.get("failure_reason")

    # If we have a valid description, use it
    if isinstance(description, str) and description.strip():
        return f"that appears as {description.strip()}"

    # If we have a failure reason, indicate it's not understood
    if isinstance(failure_reason, str) and failure_reason.strip():
        return "that is not understood"

    # Default case
    return "that is not understood"


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
    return f"[media] {ANGLE_OPEN}{base} {format_media_description(s)}{ANGLE_CLOSE}"


def format_media_sentence(kind: str, description: str | None) -> str:
    """
    Format a general media sentence with angle quotes:
      ‹the <kind> that appears as <description>›
    Falls back to 'that is not understood' when description is missing/unsupported.
    """
    media_desc = format_media_description(description or "not understood")
    return f"[media] {ANGLE_OPEN}the {kind} {media_desc}{ANGLE_CLOSE}"
