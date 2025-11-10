# media/media_format.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

logger = logging.getLogger(__name__)

"""
Formatting helpers for media descriptions and message text.

Conventions (agreed):
- Generated media descriptions are wrapped in single angle quotes: ‹ … ›
- Stickers include set/name plus a description in ‹…›.
"""

ANGLE_OPEN = "‹"
ANGLE_CLOSE = "›"


def format_media_description(description: str | None, kind: str | None = None) -> str:
    """
    Returns a clause beginning with 'that ...'.
    If there's no usable description, return a generic fallback.
    Uses appropriate verb based on media kind (audio uses 'sounds like', others use 'appears as').
    """
    s = (description or "").strip()
    if not s:
        return "that is not understood"

    # Use appropriate verb based on media kind
    if kind and kind.lower() == "audio":
        return f"that sounds like {s}"
    else:
        return f"that appears as {s}"


async def _extract_sticker_set_name(media_item, agent, resolve_sticker_set_name) -> str:
    """
    Extract sticker set name using all available methods in order of preference.

    This function tries multiple approaches:
    1. Get from MediaItem.sticker_set_name (if already resolved)
    2. Resolve via API using resolve_sticker_set_name function
    3. Return "(unknown)" as final fallback

    Args:
        media_item: The MediaItem object containing sticker information
        agent: Telegram agent for API calls
        resolve_sticker_set_name: Function to resolve sticker set names via API

    Returns:
        Resolved sticker set name, never None
    """
    # Method 1: Get sticker set name from MediaItem first
    sticker_set_name = getattr(media_item, "sticker_set_name", None)
    if sticker_set_name:
        return sticker_set_name

    # Method 2: If not in MediaItem, try to resolve from attributes via API
    try:
        sticker_set_name = await resolve_sticker_set_name(agent, media_item)
        if sticker_set_name:
            return sticker_set_name
    except Exception:
        pass

    # Method 3: Final fallback
    return "(unknown)"


def _format_sticker_sentence_internal(
    sticker_name: str, sticker_set_name: str, description: str | None
) -> str:
    """
    Internal helper for formatting sticker sentences.
    Full sticker sentence:
      the sticker `<name>` from the sticker set `<set>` that appears as ‹…›
    Falls back to 'that is not understood' when description is missing/unsupported.
    """
    base = f"the sticker `{sticker_name}` from the sticker set `{sticker_set_name}`"
    s = (description or "").strip()
    return f"⟦media⟧ {ANGLE_OPEN}{base} {format_media_description(s, 'sticker')}{ANGLE_CLOSE}"


async def format_sticker_sentence(
    media_item, agent, media_chain, resolve_sticker_set_name
) -> str:
    """
    Process a sticker MediaItem and return a formatted sentence.

    This function handles:
    - Extracting sticker name from MediaItem attributes
    - Resolving sticker set name (from MediaItem or via API)
    - Retrieving cached metadata/descriptions
    - Fallback handling for unresolved names
    - Final formatting using the internal helper

    Args:
        media_item: The MediaItem object containing sticker information
        agent: Telegram agent for API calls
        media_chain: Media cache for retrieving descriptions
        resolve_sticker_set_name: Function to resolve sticker set names via API

    Returns:
        Formatted sticker sentence ready for conversation history
    """

    # Get sticker name from MediaItem
    sticker_name = getattr(media_item, "sticker_name", None)

    # Extract sticker set name using all available methods
    sticker_set_name = await _extract_sticker_set_name(
        media_item, agent, resolve_sticker_set_name
    )

    # Check if we already have cached metadata
    meta = None
    try:
        meta = await media_chain.get(media_item.unique_id, agent=agent)
    except Exception:
        meta = None

    # Use the resolved sticker name, with fallback
    if not sticker_name:
        sticker_name = "(unnamed)"

    # Get raw description from cache for format_sticker_sentence
    desc_text = meta.get("description") if isinstance(meta, dict) else None

    return _format_sticker_sentence_internal(
        sticker_name=sticker_name,
        sticker_set_name=sticker_set_name,
        description=desc_text,
    )


def format_media_sentence(
    kind: str,
    description: str | None,
    *,
    failure_reason: str | None = None,
) -> str:
    """
    Format a general media sentence with angle quotes:
      ‹the <kind> that appears as <description>› (or 'sounds like' for audio)
    Falls back to 'that is not understood' when description is missing.
    """
    s = (description or "").strip()
    if s:
        media_desc = format_media_description(s, kind)
        return f"⟦media⟧ {ANGLE_OPEN}the {kind} {media_desc}{ANGLE_CLOSE}"

    if failure_reason:
        reason = failure_reason.strip()
        if reason:
            return (
                f"⟦media⟧ {ANGLE_OPEN}the {kind} could not be analyzed "
                f"({reason}){ANGLE_CLOSE}"
            )

    media_desc = format_media_description(None, kind)
    return f"⟦media⟧ {ANGLE_OPEN}the {kind} {media_desc}{ANGLE_CLOSE}"
