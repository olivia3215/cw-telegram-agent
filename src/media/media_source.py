# src/media/media_source.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Media source abstraction for description providers.

This module provides a clean abstraction for different sources of media descriptions,
including curated descriptions, cached AI-generated descriptions, and on-demand AI generation.

NOTE: This module now re-exports all classes and functions from media.sources for backward
compatibility. New code should import directly from media.sources.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from config import CONFIG_DIRECTORIES, STATE_DIRECTORY

# Re-export everything from sources package for backward compatibility
from .sources.ai_chain import DOWNLOAD_MEDIA_TIMEOUT_SECONDS
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
_PROFILE_PHOTO_FETCH_LAST_TS = 0.0


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

    from .media_sources import get_directory_media_source, get_resolved_state_media_path

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
    ai_cache_dir = get_resolved_state_media_path()
    if ai_cache_dir is None:
        ai_cache_dir = (Path(STATE_DIRECTORY or "state").expanduser().resolve() / "media").resolve()
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


async def get_media_bytes_from_pipeline(
    *,
    unique_id: str,
    agent: Any,
    doc: Any | None = None,
    kind: str | None = None,
    update_last_used: bool = False,
    description_budget_override: int | None = None,
) -> bytes | None:
    """
    Resolve media bytes via the default media pipeline.

    This helper intentionally lives in the media module so callers outside media
    don't need to implement directory/media-file resolution themselves.

    Args:
        unique_id: Media unique identifier.
        agent: Agent instance (required for chain lookup/download).
        doc: Optional Telegram media object used on cache miss.
        kind: Optional media kind hint passed into chain lookup.
        update_last_used: Whether to update last-used timestamp for cache hit.
        description_budget_override: Optional temporary budget value for this call.
            Use 0 to prevent description generation attempts.

    Returns:
        Media bytes if a cached/downloaded file can be resolved, else None.
    """
    if not unique_id or not agent:
        return None

    # Local imports avoid broad module coupling for callers that only need core types.
    from media.media_budget import get_remaining_description_budget, reset_description_budget
    from media.media_service import get_media_service
    from media.media_sources import iter_directory_media_sources
    from media.state_path import get_resolved_state_media_path

    budget_before: int | None = None
    if description_budget_override is not None:
        budget_before = get_remaining_description_budget()
        reset_description_budget(description_budget_override)

    try:
        media_chain = get_default_media_source_chain()
        record = await media_chain.get(
            unique_id=unique_id,
            agent=agent,
            doc=doc,
            kind=kind,
            update_last_used=update_last_used,
        )
    except Exception as e:
        logger.debug("Media pipeline lookup failed for %s: %s", unique_id, e)
        record = None
    finally:
        if description_budget_override is not None and budget_before is not None:
            reset_description_budget(budget_before)

    try:
        for source in iter_directory_media_sources():
            media_dir = getattr(source, "directory", None)
            if not media_dir:
                continue
            svc = get_media_service(media_dir)
            media_file = svc.resolve_media_file(
                unique_id,
                record if isinstance(record, dict) else None,
            )
            if media_file and media_file.exists():
                return media_file.read_bytes()
    except Exception as e:
        logger.debug("Failed resolving media bytes for %s from registered dirs: %s", unique_id, e)

    # Final state/media probe in case registry has not been initialized with it yet.
    try:
        state_media_dir = get_resolved_state_media_path()
        if state_media_dir:
            svc = get_media_service(state_media_dir)
            media_file = svc.resolve_media_file(unique_id, None)
            if media_file and media_file.exists():
                return media_file.read_bytes()
    except Exception as e:
        logger.debug("Failed resolving media bytes for %s from state/media: %s", unique_id, e)

    return None


async def cache_media_bytes_in_pipeline(
    *,
    unique_id: str,
    agent: Any,
    media_bytes: bytes,
    kind: str = "photo",
    mime_type: str | None = None,
) -> None:
    """
    Persist media bytes into the default pipeline cache.

    This stores metadata plus media file bytes so later reads can resolve via
    `get_media_bytes_from_pipeline(...)` without re-downloading from Telegram.
    """
    if not unique_id or not agent or not media_bytes:
        return

    logger.info(
        "MEDIA_TRACE PIPELINE_CACHE_REQUEST unique_id=%s (cache_media_bytes_in_pipeline)",
        unique_id,
    )
    from media.mime_utils import get_file_extension_from_mime_or_bytes

    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
    record: dict[str, Any] = {
        "unique_id": str(unique_id),
        "kind": kind,
        "status": MediaStatus.BUDGET_EXHAUSTED.value,
        "description": None,
        "mime_type": mime_type,
        "description_retry_count": 0,
    }

    try:
        media_chain = get_default_media_source_chain()
        await media_chain.put(
            str(unique_id),
            record,
            media_bytes=media_bytes,
            file_extension=file_extension,
            agent=agent,
        )
    except Exception as e:
        logger.debug("Failed to cache media bytes for %s: %s", unique_id, e)


async def get_profile_photo_bytes_from_pipeline(
    *,
    unique_id: str | None,
    agent: Any,
    client: Any,
    entity: Any | None = None,
    photo_obj: Any | None = None,
    description_budget_override: int | None = 0,
    min_fetch_interval_seconds: float = 0.75,
    allow_profile_photos_fallback: bool = True,
) -> bytes | None:
    """
    Resolve profile-photo bytes with cache-first behavior and rate-limited fallback fetch.

    This helper avoids repeated `GetUserPhotosRequest` calls by:
    1) checking pipeline caches first with doc=None
    2) only calling get_profile_photos(limit=1) on cache miss
    3) enforcing a minimum interval between fallback get_profile_photos calls
    """
    from telegram_download import download_media_bytes
    from telegram_media import get_unique_id

    # 1) Cache-only lookup first (no Telegram fetch).
    if unique_id:
        cached = await get_media_bytes_from_pipeline(
            unique_id=str(unique_id),
            agent=agent,
            doc=None,
            kind="photo",
            update_last_used=True,
            description_budget_override=description_budget_override,
        )
        if cached:
            return cached

    # 2) Try provided photo object (if already a downloadable type). Cache on success.
    if photo_obj is not None:
        try:
            direct = await asyncio.wait_for(
                download_media_bytes(client, photo_obj),
                timeout=DOWNLOAD_MEDIA_TIMEOUT_SECONDS,
            )
            if direct and unique_id and agent:
                await cache_media_bytes_in_pipeline(
                    unique_id=str(unique_id),
                    agent=agent,
                    media_bytes=direct,
                    kind="photo",
                )
            if direct:
                return direct
        except Exception:
            pass

    # 3) Optional rate-limited fallback fetch: get_profile_photos(limit=1).
    if not allow_profile_photos_fallback:
        return None

    if entity is None:
        return None

    global _PROFILE_PHOTO_FETCH_LAST_TS
    now = time.monotonic()
    wait_s = min_fetch_interval_seconds - (now - _PROFILE_PHOTO_FETCH_LAST_TS)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    _PROFILE_PHOTO_FETCH_LAST_TS = time.monotonic()

    try:
        photos = await client.get_profile_photos(entity, limit=1)
    except TypeError:
        photos = await client.get_profile_photos(entity)
    except Exception as e:
        logger.debug("Rate-limited profile photo fetch failed: %s", e)
        return None

    if not photos:
        return None

    resolved = photos[0]
    resolved_uid = get_unique_id(resolved)
    if resolved_uid:
        via_pipeline = await get_media_bytes_from_pipeline(
            unique_id=str(resolved_uid),
            agent=agent,
            doc=resolved,
            kind="photo",
            update_last_used=True,
            description_budget_override=description_budget_override,
        )
        if via_pipeline:
            return via_pipeline

    try:
        return await asyncio.wait_for(
            download_media_bytes(client, resolved),
            timeout=DOWNLOAD_MEDIA_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug("Fallback resolved profile photo download failed: %s", e)
        return None
