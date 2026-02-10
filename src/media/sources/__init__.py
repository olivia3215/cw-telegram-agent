# src/media/sources/__init__.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Media source implementations.

This package contains all media source implementations, split from the original
monolithic media_source.py file for better organization and maintainability.
"""

from .ai_chain import AIChainMediaSource
from .ai_generating import AIGeneratingMediaSource
from .base import (
    MEDIA_FILE_EXTENSIONS,
    MediaSource,
    MediaStatus,
    fallback_sticker_description,
    get_describe_timeout_secs,
    get_emoji_unicode_name,
)
from .budget import BudgetExhaustedMediaSource
from .composite import CompositeMediaSource
from .directory import DirectoryMediaSource
from .helpers import make_error_record
from .nothing import NothingMediaSource
from .unsupported import UnsupportedFormatMediaSource

__all__ = [
    "AIChainMediaSource",
    "AIGeneratingMediaSource",
    "BudgetExhaustedMediaSource",
    "CompositeMediaSource",
    "DirectoryMediaSource",
    "MediaSource",
    "MediaStatus",
    "MEDIA_FILE_EXTENSIONS",
    "NothingMediaSource",
    "UnsupportedFormatMediaSource",
    "fallback_sticker_description",
    "get_describe_timeout_secs",
    "get_emoji_unicode_name",
    "make_error_record",
]

