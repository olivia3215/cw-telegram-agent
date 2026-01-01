# media/sources/base.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Base classes and common utilities for media sources.

This module provides the abstract base class for all media sources,
status enumeration, and helper functions.
"""

import unicodedata
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from ..mime_utils import is_tgs_mime_type

# Media file extensions supported by the system
MEDIA_FILE_EXTENSIONS = [
    ".webp",
    ".tgs",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
]

# Timeout for LLM description
_DESCRIBE_TIMEOUT_SECS = 12


class MediaStatus(Enum):
    """Standardized status values for media records."""

    GENERATED = "generated"  # AI successfully generated description
    BUDGET_EXHAUSTED = "budget_exhausted"  # Budget limits reached (temporary)
    UNSUPPORTED = "unsupported"  # Media format not supported (permanent)
    TEMPORARY_FAILURE = "temporary_failure"  # Download failed, timeout, etc.
    PERMANENT_FAILURE = "permanent_failure"  # API misuse, permanent errors

    @classmethod
    def is_temporary_failure(cls, status):
        """Check if a status represents a temporary failure that should be retried."""
        if isinstance(status, cls):
            return status in [cls.BUDGET_EXHAUSTED, cls.TEMPORARY_FAILURE]
        return status in [cls.BUDGET_EXHAUSTED.value, cls.TEMPORARY_FAILURE.value]

    @classmethod
    def is_permanent_failure(cls, status):
        """Check if a status represents a permanent failure that should not be retried."""
        if isinstance(status, cls):
            return status in [cls.UNSUPPORTED, cls.PERMANENT_FAILURE]
        return status in [cls.UNSUPPORTED.value, cls.PERMANENT_FAILURE.value]

    @classmethod
    def is_successful(cls, status):
        """Check if a status represents successful generation."""
        if isinstance(status, cls):
            return status == cls.GENERATED
        return status == cls.GENERATED.value


class MediaSource(ABC):
    """
    Base class for all media description sources.

    Each source can provide media descriptions and return None if not found.
    Sources are composed into chains where earlier sources take precedence.
    """

    @abstractmethod
    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        kind: str | None = None,
        sticker_set_name: str | None = None,
        sticker_name: str | None = None,
        **metadata,
    ) -> dict[str, Any] | None:
        """
        Retrieve a media description record by its unique ID.

        Args:
            unique_id: The Telegram file unique ID
            agent: The agent instance (for accessing client, LLM, etc.)
            doc: The Telegram document reference (for downloading)
            kind: Media type (sticker, photo, gif, animation, video, animated_sticker)
            sticker_set_name: Sticker set name (if applicable)
            sticker_name: Sticker name/emoji (if applicable)
            **metadata: Additional metadata (sender_id, channel_id, etc.)

        Returns:
            The full record dict if known, else None.
        """
        ...


# Helper functions for checking media types (works with string kind values from records)
def _needs_video_analysis(kind: str | None, mime_type: str | None) -> bool:
    """
    Check if media should use video description API.

    Returns True for:
    - Videos and animations (by kind)
    - TGS animated stickers (sticker kind + gzip mime)
    """
    if kind in ("video", "animation"):
        return True
    if kind == "sticker" and mime_type:
        return is_tgs_mime_type(mime_type)
    return False


def get_emoji_unicode_name(emoji: str) -> str:
    """Get Unicode name(s) for an emoji, handling multi-character emojis."""
    names = []
    for char in emoji:
        try:
            name = unicodedata.name(char)
            names.append(name.lower())
        except ValueError:
            # Some characters don't have names
            names.append(f"u+{ord(char):04x}")
    return " + ".join(names)


def fallback_sticker_description(
    sticker_name: str | None, *, animated: bool = True
) -> str:
    """
    Create a fallback description for a sticker.

    Args:
        sticker_name: The sticker emoji/name
        animated: Whether this is an animated sticker (default: True)

    Returns:
        A formatted description string with emoji and unicode name in parentheses
    """
    prefix = "an animated sticker" if animated else "a sticker"

    if sticker_name:
        try:
            emoji_description = get_emoji_unicode_name(sticker_name)
            return f"{prefix}: {sticker_name} ({emoji_description})"
        except Exception:
            # If we can't get emoji description, just use the name
            return f"{prefix}: {sticker_name}"
    else:
        # No sticker name provided
        return prefix


def get_describe_timeout_secs() -> int:
    """Get the timeout for LLM description calls."""
    return _DESCRIBE_TIMEOUT_SECS

