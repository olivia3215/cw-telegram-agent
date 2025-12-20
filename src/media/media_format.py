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


async def _extract_sticker_set_metadata(media_item, agent, resolve_sticker_metadata) -> tuple[str, str]:
    """
    Extract sticker set metadata using all available methods in order of preference.

    This function tries multiple approaches:
    1. Get from MediaItem (if already resolved)
    2. Resolve via API using resolve_sticker_metadata function
    3. Return "(unknown)" as final fallback

    Args:
        media_item: The MediaItem object containing sticker information
        agent: Telegram agent for API calls
        resolve_sticker_metadata: Function to resolve sticker set metadata via API

    Returns:
        Tuple of (sticker_set_name, sticker_set_title), never None
    """
    # Method 1: Get from MediaItem first
    sticker_set_name = getattr(media_item, "sticker_set_name", None)
    sticker_set_title = getattr(media_item, "sticker_set_title", None)

    if sticker_set_name and sticker_set_title:
        return sticker_set_name, sticker_set_title

    # Method 2: If not in MediaItem, try to resolve via API
    try:
        name, title = await resolve_sticker_metadata(agent, media_item)
        return name or sticker_set_name or "(unknown)", title or sticker_set_title or "(unknown)"
    except Exception:
        pass

    # Method 3: Final fallback
    return sticker_set_name or "(unknown)", sticker_set_title or "(unknown)"


def _format_sticker_sentence_internal(
    sticker_name: str, sticker_set_name: str, sticker_set_title: str, description: str | None
) -> str:
    """
    Internal helper for formatting sticker sentences.
    Full sticker sentence:
      the sticker `<name>` from the sticker set `<title>` (`<name>`) that appears as ‹…›
    Falls back to 'that is not understood' when description is missing/unsupported.
    """
    set_desc = f"`{sticker_set_title}` (`{sticker_set_name}`)" if sticker_set_title != sticker_set_name else f"`{sticker_set_name}`"
    base = f"the sticker `{sticker_name}` from the sticker set {set_desc}"
    s = (description or "").strip()
    return f"⟦media⟧ {ANGLE_OPEN}{base} {format_media_description(s, 'sticker')}{ANGLE_CLOSE}"


async def format_sticker_sentence(
    media_item, agent, media_chain, resolve_sticker_metadata
) -> str:
    """
    Process a sticker MediaItem and return a formatted sentence.

    This function handles:
    - Extracting sticker name from MediaItem attributes
    - Resolving sticker set metadata (from MediaItem or via API)
    - Retrieving cached metadata/descriptions
    - Fallback handling for unresolved names
    - Final formatting using the internal helper

    Args:
        media_item: The MediaItem object containing sticker information
        agent: Telegram agent for API calls
        media_chain: Media cache for retrieving descriptions
        resolve_sticker_metadata: Function to resolve sticker set metadata via API

    Returns:
        Formatted sticker sentence ready for conversation history
    """

    # Get sticker name from MediaItem
    sticker_name = getattr(media_item, "sticker_name", None)

    # Extract sticker set metadata using all available methods
    sticker_set_name, sticker_set_title = await _extract_sticker_set_metadata(
        media_item, agent, resolve_sticker_metadata
    )

    # Check if we already have cached metadata
    meta = None
    try:
        meta = await media_chain.get(media_item.unique_id, agent=agent)
        if isinstance(meta, dict):
            # If we got a cached title, prefer it over what we just resolved
            # (cached title might be from a previous successful API call)
            cached_title = meta.get("sticker_set_title")
            if cached_title:
                sticker_set_title = cached_title
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
        sticker_set_title=sticker_set_title,
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
