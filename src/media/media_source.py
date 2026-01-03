# media/media_source.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Media source abstraction for description providers.

This module provides a clean abstraction for different sources of media descriptions,
including curated descriptions, cached AI-generated descriptions, and on-demand AI generation.

NOTE: This module now re-exports all classes and functions from media.sources for backward
compatibility. New code should import directly from media.sources.
"""

import logging
from pathlib import Path

from config import CONFIG_DIRECTORIES, STATE_DIRECTORY

# Re-export everything from sources package for backward compatibility
from .sources import (
    AIChainMediaSource,
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    CompositeMediaSource,
    DirectoryMediaSource,
    MEDIA_FILE_EXTENSIONS,
    MediaSource,
    MediaStatus,
    NothingMediaSource,
    UnsupportedFormatMediaSource,
    fallback_sticker_description,
    get_describe_timeout_secs,
    get_emoji_unicode_name,
    make_error_record,
)

logger = logging.getLogger(__name__)

# ---------- singleton helpers ----------
_GLOBAL_DEFAULT_CHAIN: CompositeMediaSource | None = None


def get_default_media_source_chain() -> CompositeMediaSource:
    """
    Get the global default media source chain singleton.

    This chain includes:
    1. Curated descriptions from all config directories
    2. Cached AI-generated descriptions
    3. Budget management
    4. AI generation fallback
    """
    global _GLOBAL_DEFAULT_CHAIN
    if _GLOBAL_DEFAULT_CHAIN is None:
        _GLOBAL_DEFAULT_CHAIN = _create_default_chain()
    return _GLOBAL_DEFAULT_CHAIN


def _create_default_chain() -> CompositeMediaSource:
    """
    Create the default media source chain.

    Internal helper for get_default_media_source_chain.
    """

    from .media_sources import get_directory_media_source

    sources: list[MediaSource] = []

    # Add config directories (curated descriptions) - checked first
    for config_dir in CONFIG_DIRECTORIES:
        media_dir = Path(config_dir) / "media"
        if media_dir.exists() and media_dir.is_dir():
            sources.append(get_directory_media_source(media_dir))
            logger.info(f"Added curated media directory: {media_dir}")

    # Set up AI cache - always use MySQL for metadata (media files stay on disk)
    # Always set up filesystem directory for AIGeneratingMediaSource (for debug saves)
    # Also always register it so it appears in the media editor directory list
    state_dir = Path(STATE_DIRECTORY)
    ai_cache_dir = state_dir / "media"
    ai_cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Always register the directory source so it appears in scan_media_directories()
    # Media files are always stored on disk, even when MySQL is used for metadata
    directory_source = get_directory_media_source(ai_cache_dir)
    logger.info(f"Registered AI cache directory: {ai_cache_dir}")
    
    # MySQL is required - verify configuration at startup
    from config import MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD
    if not all([MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD]):
        raise RuntimeError(
            "MySQL configuration incomplete. "
            "Please set CINDY_AGENT_MYSQL_DATABASE, CINDY_AGENT_MYSQL_USER, and CINDY_AGENT_MYSQL_PASSWORD. "
            "MySQL is required for media metadata storage."
        )
    
    from media.mysql_media_source import MySQLMediaSource
    # Pass directory_source so MySQLMediaSource can write media files to disk
    ai_cache_source = MySQLMediaSource(directory_source=directory_source)
    logger.info("Added MySQL media cache source")

    # Add AI chain source that orchestrates unsupported/budget/AI generation
    sources.append(
        AIChainMediaSource(
            cache_source=ai_cache_source,
            unsupported_source=UnsupportedFormatMediaSource(),
            budget_source=BudgetExhaustedMediaSource(),
            ai_source=AIGeneratingMediaSource(cache_directory=ai_cache_dir),
        )
    )

    return CompositeMediaSource(sources)
