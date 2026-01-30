# media/sources/ai_chain.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
AI chain media source.

Orchestrates caching and chaining of media sources with proper temporary failure handling.

This source manages the flow between:
1. Cache source (for persistent storage)
2. Unsupported format source (to avoid budget consumption)
3. Budget source (to limit processing)
4. AI generating source (for actual description generation)
"""

import glob as glob_module
import inspect
import logging
from pathlib import Path
from typing import Any

from config import STATE_DIRECTORY
from telegram_download import download_media_bytes

from ..mime_utils import get_file_extension_from_mime_or_bytes
from .base import MediaSource, MediaStatus, get_max_description_retries
from .directory import DirectoryMediaSource

logger = logging.getLogger(__name__)

class AIChainMediaSource(MediaSource):
    """
    Orchestrates caching and chaining of media sources with proper temporary failure handling.

    This source manages the flow between:
    1. Cache source (for persistent storage)
    2. Unsupported format source (to avoid budget consumption)
    3. Budget source (to limit processing)
    4. AI generating source (for actual description generation)

    Key behaviors:
    - Non-temporary cached records are returned immediately
    - Temporary failures (budget exhaustion, timeouts) are retried
    - Avoids storing new temporary failures when replacing cached temporary failures
    - Optimizes downloads by passing doc=None when media is already cached
    """

    def __init__(
        self,
        cache_source: MediaSource,
        unsupported_source: MediaSource,
        budget_source: MediaSource,
        ai_source: MediaSource,
    ):
        """
        Initialize the AI chain source.

        Args:
            cache_source: Source for persistent cache storage
            unsupported_source: Source to check unsupported formats
            budget_source: Source to manage budget limits
            ai_source: Source for AI generation
        """
        self.cache_source = cache_source
        self.unsupported_source = unsupported_source
        self.budget_source = budget_source
        self.ai_source = ai_source

    async def _store_record(
        self,
        unique_id: str,
        record: dict[str, Any],
        media_bytes: bytes | None,
        file_extension: str | None,
        agent: Any,
    ) -> None:
        """Store record and optional media file to cache source."""
        if inspect.iscoroutinefunction(self.cache_source.put):
            await self.cache_source.put(
                unique_id, record, media_bytes, file_extension, agent=agent
            )
        else:
            self.cache_source.put(
                unique_id, record, media_bytes, file_extension, agent=agent
            )

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        **metadata,
    ) -> dict[str, Any]:
        """
        Get media description with proper caching and temporary failure handling.

        Returns:
            Media record with description, status, and metadata
        """
        # 1. Try cache first
        cached_record = await self.cache_source.get(
            unique_id, agent, doc, **metadata
        )

        # 2. If we have a cached record, decide whether to return it or retry
        if cached_record:
            status = cached_record.get("status")
            # If successful or permanent failure, we will return cached record
            # BUT first check if media file exists on disk - if not and we have doc,
            # download and store it (metadata may exist without the actual file)
            if not MediaStatus.is_temporary_failure(status):
                # Check if media file exists - may have metadata but no file
                # Prefer media_file from record (handles .flac, .zip, .bin etc.)
                cache_dir = None
                if isinstance(self.cache_source, DirectoryMediaSource):
                    cache_dir = self.cache_source.directory
                else:
                    cache_dir = Path(STATE_DIRECTORY) / "media"
                media_file_exists = False
                if cache_dir:
                    # Prefer media_file from record (handles any extension the writer emits)
                    media_file_name = cached_record.get("media_file")
                    if media_file_name:
                        media_path = cache_dir / media_file_name
                        if media_path.exists() and media_path.is_file():
                            media_file_exists = True
                    if not media_file_exists:
                        # Fallback: glob for unique_id.* (any extension except .json)
                        escaped = glob_module.escape(unique_id)
                        for path in cache_dir.glob(f"{escaped}.*"):
                            if path.suffix.lower() != ".json":
                                media_file_exists = True
                                break
                if not media_file_exists and doc is not None and agent is not None:
                    try:
                        logger.debug(
                            f"AIChainMediaSource: downloading missing media file for cached {unique_id}"
                        )
                        media_bytes = await download_media_bytes(agent.client, doc)
                        mime_type = getattr(doc, "mime_type", None)
                        file_extension = get_file_extension_from_mime_or_bytes(
                            mime_type, media_bytes
                        )
                        await self._store_record(
                            unique_id, cached_record, media_bytes, file_extension, agent
                        )
                    except Exception as e:
                        logger.warning(
                            f"AIChainMediaSource: failed to download missing media for {unique_id}: {e}"
                        )
                return cached_record

            # If it's a temporary failure, we only retry if we have a document
            # to attempt a new description generation. Without a document
            # (e.g. during lookup-only formatting phase), we return what we have.
            if doc is None:
                return cached_record

            # Don't retry if we've exceeded the retry limit
            retry_count = cached_record.get("description_retry_count", 0)
            if retry_count >= get_max_description_retries():
                logger.debug(
                    f"AIChainMediaSource: {unique_id} at retry limit ({retry_count}), not retrying"
                )
                return cached_record

        # 3. Chain through sources
        record = None

        for source in [self.unsupported_source, self.budget_source, self.ai_source]:
            # Always pass doc to sources - they can decide whether to use it
            record = await source.get(unique_id, agent, doc, **metadata)
            if record:
                break

        # 4. Download and store media file if needed (even when budget exhausted)
        # We always want to store the media file so we can describe it later without re-downloading
        media_bytes = None
        file_extension = None

        # Check if we need to download and store the media file
        # Always check if media file exists on disk, even for cached records
        # (records might have _on_disk=True but no actual media file)
        # Note: Media files are always stored on disk, even when MySQL is used for metadata
        # Prefer media_file from record; fallback glob handles any extension
        media_file_exists = False
        
        # Determine the cache directory to check
        cache_dir = None
        if isinstance(self.cache_source, DirectoryMediaSource):
            cache_dir = self.cache_source.directory
        else:
            # For MySQLMediaSource or other sources, check the default AI cache directory
            # (media files are always stored on disk, not in MySQL)
            cache_dir = Path(STATE_DIRECTORY) / "media"
        
        if cache_dir:
            # Prefer media_file from record (handles any extension the writer emits)
            for rec in (cached_record, record):
                if rec:
                    media_file_name = rec.get("media_file")
                    if media_file_name:
                        media_path = cache_dir / media_file_name
                        if media_path.exists() and media_path.is_file():
                            media_file_exists = True
                            break
            if not media_file_exists:
                # Fallback: glob for unique_id.* (any extension except .json)
                escaped = glob_module.escape(unique_id)
                for path in cache_dir.glob(f"{escaped}.*"):
                    if path.suffix.lower() != ".json":
                        media_file_exists = True
                        break

        # Download media if we have a doc and media file doesn't exist
        # Always attempt download if we have doc, regardless of budget status or _on_disk flag
        if not media_file_exists and doc is not None and agent is not None:
                try:
                    logger.debug(
                        f"AIChainMediaSource: downloading media for {unique_id}"
                    )
                    media_bytes = await download_media_bytes(agent.client, doc)

                    # Get file extension from MIME type or by detecting from bytes
                    mime_type = getattr(doc, "mime_type", None)
                    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)

                    logger.debug(
                        f"AIChainMediaSource: downloaded {len(media_bytes)} bytes for {unique_id}, extension: {file_extension}"
                    )
                except Exception as e:
                    logger.warning(
                        f"AIChainMediaSource: failed to download media for {unique_id}: {e}"
                    )
                    # Continue without media file - metadata is still valuable

        # 5. Store metadata record if we got a new record or if we downloaded media file
        # Always store if we downloaded the media file (even if replacing a cached record)
        # Also store if we got a new record that's not already on disk
        # If we downloaded media but have no record (all sources returned None), create
        # a minimal record so the file gets saved for later description generation
        record_to_store = record
        if record is None and media_bytes is not None:
            record_to_store = {
                "unique_id": unique_id,
                "kind": metadata.get("kind", "photo"),
                "status": MediaStatus.BUDGET_EXHAUSTED.value,
                "description": None,
                "sticker_set_name": metadata.get("sticker_set_name"),
                "sticker_name": metadata.get("sticker_name"),
                "mime_type": metadata.get("mime_type"),
                "description_retry_count": 0,
            }

        # Set description_retry_count: 0 on success, increment only on TEMPORARY_FAILURE
        # (BUDGET_EXHAUSTED preserves count - we didn't actually attempt description)
        if record_to_store:
            prev_count = (cached_record or {}).get("description_retry_count", 0)
            if MediaStatus.is_successful(record_to_store.get("status")):
                record_to_store["description_retry_count"] = 0
            elif record_to_store.get("status") in (
                MediaStatus.TEMPORARY_FAILURE.value,
                MediaStatus.TEMPORARY_FAILURE,
            ):
                record_to_store["description_retry_count"] = prev_count + 1
            else:
                # BUDGET_EXHAUSTED etc - preserve count
                record_to_store["description_retry_count"] = prev_count

        if record_to_store and (
            media_bytes is not None or not record_to_store.get("_on_disk", False)
        ):
            should_store = True

            # Don't store if it's another temporary failure replacing a cached temporary failure
            # UNLESS we downloaded the media file (in which case we want to preserve it)
            # OR description_retry_count increased (we must persist the increment for retry limit)
            if (
                record is not None
                and cached_record
                and MediaStatus.is_temporary_failure(cached_record.get("status"))
                and MediaStatus.is_temporary_failure(record.get("status"))
                and media_bytes is None  # Only skip if we didn't download media
                and record_to_store.get("description_retry_count", 0)
                <= cached_record.get("description_retry_count", 0)
            ):
                should_store = False

            if should_store:
                await self._store_record(
                    unique_id, record_to_store, media_bytes, file_extension, agent
                )

        # Update last_used_at when returning a record from chain (cache hit was
        # already updated by MySQLMediaSource.get)
        if record and metadata.get("update_last_used"):
            try:
                from db import media_metadata
                media_metadata.update_media_last_used(unique_id)
            except Exception as e:
                logger.debug(f"AIChainMediaSource: failed to update last_used for {unique_id}: {e}")

        return record

