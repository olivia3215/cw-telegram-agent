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

import inspect
import logging
from pathlib import Path
from typing import Any

from config import STATE_DIRECTORY
from telegram_download import download_media_bytes

from ..mime_utils import get_file_extension_from_mime_or_bytes
from .base import MediaSource, MediaStatus, MEDIA_FILE_EXTENSIONS
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
            # If successful or permanent failure, always return cached record
            if not MediaStatus.is_temporary_failure(status):
                return cached_record

            # If it's a temporary failure, we only retry if we have a document
            # to attempt a new description generation. Without a document
            # (e.g. during lookup-only formatting phase), we return what we have.
            if doc is None:
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
        media_file_exists = False
        
        # Determine the cache directory to check
        cache_dir = None
        if isinstance(self.cache_source, DirectoryMediaSource):
            cache_dir = self.cache_source.directory
        else:
            # For MySQLMediaSource or other sources, check the default AI cache directory
            # (media files are always stored on disk, not in MySQL)
            from config import STATE_DIRECTORY
            from pathlib import Path
            cache_dir = Path(STATE_DIRECTORY) / "media"
        
        if cache_dir:
            # Check if media file already exists
            for ext in MEDIA_FILE_EXTENSIONS:
                media_file = cache_dir / f"{unique_id}{ext}"
                if media_file.exists():
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
        if record and (media_bytes is not None or not record.get("_on_disk", False)):
            should_store = True

            # Don't store if it's another temporary failure replacing a cached temporary failure
            # UNLESS we downloaded the media file (in which case we want to preserve it)
            if (
                cached_record
                and MediaStatus.is_temporary_failure(cached_record.get("status"))
                and MediaStatus.is_temporary_failure(record.get("status"))
                and media_bytes is None  # Only skip if we didn't download media
            ):
                should_store = False

            if should_store:
                # Store record with optional media file
                # If we have media_bytes, this will add media_file to the record
                # Handle both sync and async put methods
                import inspect
                if inspect.iscoroutinefunction(self.cache_source.put):
                    await self.cache_source.put(unique_id, record, media_bytes, file_extension, agent=agent)
                else:
                    self.cache_source.put(unique_id, record, media_bytes, file_extension, agent=agent)

        return record

