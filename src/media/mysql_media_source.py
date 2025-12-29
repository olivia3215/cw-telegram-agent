# media/mysql_media_source.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
MySQL-backed media source for cached AI-generated descriptions.
"""

import logging
from typing import Any

from media.media_source import MediaSource

logger = logging.getLogger(__name__)


class MySQLMediaSource(MediaSource):
    """
    Media source that reads from MySQL database.
    
    Only stores core/media-specific fields (excludes agent, channel, timestamps, etc.).
    """

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
        Store a media description record in MySQL.
        
        Only stores core/media-specific fields. Filters out agent, channel, timestamps, etc.
        
        Args:
            unique_id: The Telegram file unique ID
            record: Media record dictionary
            media_bytes: Media file bytes (ignored - media files stay in filesystem)
            file_extension: File extension (ignored)
        """
        try:
            from db import media_metadata
            
            # Filter record to only include core/media-specific fields
            filtered_record = self._filter_core_fields(record)
            filtered_record["unique_id"] = unique_id
            
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
        - sticker_set_name (for stickers)
        - sticker_name (for stickers)
        - description
        - status
        - duration
        - mime_type
        - is_emoji_set (for stickers)
        - sticker_set_title (for stickers)
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
        
        # Sticker-specific fields (only keep if kind is sticker)
        sticker_fields = {
            "sticker_set_name",
            "sticker_name",
            "is_emoji_set",
            "sticker_set_title",
        }
        
        filtered = {}
        kind = record.get("kind")
        is_sticker = kind == "sticker"
        
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

