# media/mysql_media_source.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
MySQL-backed media source for cached AI-generated descriptions.
"""

import contextlib
import logging
from pathlib import Path
from typing import Any

from media.media_source import MediaSource

logger = logging.getLogger(__name__)


class MySQLMediaSource(MediaSource):
    """
    Media source that reads from MySQL database.
    
    Only stores core/media-specific fields (excludes agent, channel, timestamps, etc.).
    Media files are stored on disk via a DirectoryMediaSource.
    """

    def __init__(self, directory_source=None):
        """
        Initialize MySQL media source.
        
        Args:
            directory_source: Optional DirectoryMediaSource for storing media files on disk.
                             If provided, media_bytes will be written to disk in its directory.
                             The directory_source is used to get the target directory path.
        """
        self.directory_source = directory_source

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
        Retrieve a media description record from MySQL by its unique ID.
        
        Args:
            unique_id: The Telegram file unique ID
            agent: The agent instance (ignored for MySQL source)
            doc: The Telegram document reference (ignored)
            kind: Media type (ignored for lookup)
            sticker_set_name: Sticker set name (ignored for lookup)
            sticker_name: Sticker name (ignored for lookup)
            **metadata: Additional metadata (ignored for lookup)
        
        Returns:
            The media record dict if found, else None.
        """
        try:
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
            if record:
                logger.debug(f"MySQLMediaSource: cache hit for {unique_id}")
                return record
        except Exception as e:
            logger.debug(f"MySQLMediaSource: error loading {unique_id}: {e}")
        
        return None

    async def put(
        self,
        unique_id: str,
        record: dict[str, Any],
        media_bytes: bytes = None,
        file_extension: str = None,
        agent: Any = None,
    ) -> None:
        """
        Store a media description record in MySQL and optionally write media file to disk.
        
        Only stores core/media-specific fields. Filters out agent, channel, timestamps, etc.
        Media files are written to disk via directory_source if provided.
        
        Args:
            unique_id: The Telegram file unique ID
            record: Media record dictionary
            media_bytes: Media file bytes (written to disk via directory_source if provided)
            file_extension: File extension (used when writing media file to disk)
            agent: Agent instance (passed to directory_source if provided)
        """
        # Write media file to disk if we have media_bytes and a directory_source
        # We write only the media file, not the JSON metadata (which goes to MySQL)
        # If file_extension is not provided, try to determine it from media_bytes
        media_filename = None
        if media_bytes is not None and self.directory_source is not None:
            # Determine file extension if not provided
            if not file_extension:
                try:
                    from .mime_utils import get_file_extension_from_mime_or_bytes
                    # Try to get extension from record's mime_type or from media_bytes
                    mime_type = record.get("mime_type")
                    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                    if not file_extension:
                        logger.warning(
                            f"MySQLMediaSource: could not determine file extension for {unique_id}, "
                            "media file will not be written"
                        )
                except Exception as e:
                    logger.warning(
                        f"MySQLMediaSource: failed to determine file extension for {unique_id}: {e}"
                    )
            
            # Write file if we have a valid extension
            if file_extension:
                try:
                    media_dir = self.directory_source.directory
                    media_dir.mkdir(parents=True, exist_ok=True)
                    media_filename = f"{unique_id}{file_extension}"
                    media_file = media_dir / media_filename
                    temp_media_file = media_file.with_name(f"{media_file.name}.tmp")
                    try:
                        temp_media_file.write_bytes(media_bytes)
                        temp_media_file.replace(media_file)
                        logger.debug(f"MySQLMediaSource: wrote media file {media_filename} to disk")
                    except Exception:
                        # Clean up any temporary file and propagate the failure
                        with contextlib.suppress(FileNotFoundError, PermissionError):
                            temp_media_file.unlink()
                        raise
                except Exception as e:
                    logger.error(f"MySQLMediaSource: failed to write media file for {unique_id} to disk: {e}")
                    # Continue to store metadata even if file write fails
                    media_filename = None
        
        # Store metadata in MySQL
        try:
            from db import media_metadata
            
            # Filter record to only include core/media-specific fields
            filtered_record = self._filter_core_fields(record)
            filtered_record["unique_id"] = unique_id
            
            # Update media_file in filtered_record if we wrote a media file
            if media_filename:
                filtered_record["media_file"] = media_filename
            
            media_metadata.save_media_metadata(filtered_record)
            logger.debug(f"MySQLMediaSource: cached {unique_id} to MySQL")
        except Exception as e:
            logger.error(f"MySQLMediaSource: failed to cache {unique_id} to MySQL: {e}")

    def _filter_core_fields(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Filter record to only include core fields appropriate for MySQL storage.
        
        Core fields kept:
        - unique_id
        - kind
        - sticker_set_name (for stickers and animated_sticker)
        - sticker_name (for stickers and animated_sticker)
        - description
        - status
        - duration
        - mime_type
        - is_emoji_set (for stickers and animated_sticker)
        - sticker_set_title (for stickers and animated_sticker)
        - media_file
        
        Fields removed:
        - ts
        - sender_id
        - sender_name
        - channel_id
        - channel_name
        - media_ts
        - skip_fallback
        - _on_disk
        - agent_telegram_id
        """
        # Fields to always exclude
        excluded_fields = {
            "ts",
            "sender_id",
            "sender_name",
            "channel_id",
            "channel_name",
            "media_ts",
            "skip_fallback",
            "_on_disk",
            "agent_telegram_id",
        }
        
        # Sticker-specific fields (only keep if kind is sticker or animated_sticker)
        sticker_fields = {
            "sticker_set_name",
            "sticker_name",
            "is_emoji_set",
            "sticker_set_title",
        }
        
        filtered = {}
        kind = record.get("kind")
        is_sticker = kind == "sticker" or kind == "animated_sticker"
        
        # Iterate over the record once to preserve original order
        for key, value in record.items():
            # Skip excluded fields
            if key in excluded_fields:
                continue
            
            # Skip sticker-specific fields if this is not a sticker
            if not is_sticker and key in sticker_fields:
                continue
            
            # Include all other fields (core fields, sticker fields if sticker, and other fields)
            filtered[key] = value
        
        return filtered

