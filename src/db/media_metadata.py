# db/media_metadata.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for media metadata.
"""

import logging
from typing import Any

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def load_media_metadata(unique_id: str) -> dict[str, Any] | None:
    """
    Load media metadata by unique ID.
    
    Args:
        unique_id: The media unique ID
        
    Returns:
        Media metadata dictionary or None if not found
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT unique_id, kind, description, status, duration, mime_type,
                       media_file, sticker_set_name, sticker_name, is_emoji_set,
                       sticker_set_title, description_retry_count, last_used_at
                FROM media_metadata
                WHERE unique_id = %s
                """,
                (unique_id,),
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            record = {
                "unique_id": row["unique_id"],
            }
            
            if row["kind"]:
                record["kind"] = row["kind"]
            if row["description"]:
                record["description"] = row["description"]
            if row["status"]:
                record["status"] = row["status"]
            if row["duration"] is not None:
                record["duration"] = row["duration"]
            if row["mime_type"]:
                record["mime_type"] = row["mime_type"]
            if row["media_file"]:
                record["media_file"] = row["media_file"]
            if row["sticker_set_name"]:
                record["sticker_set_name"] = row["sticker_set_name"]
            if row["sticker_name"]:
                record["sticker_name"] = row["sticker_name"]
            if row["is_emoji_set"] is not None:
                record["is_emoji_set"] = bool(row["is_emoji_set"])
            if row["sticker_set_title"]:
                record["sticker_set_title"] = row["sticker_set_title"]
            if row.get("description_retry_count") is not None:
                record["description_retry_count"] = int(row["description_retry_count"])
            if row.get("last_used_at") is not None:
                record["last_used_at"] = row["last_used_at"]
            
            return record
        finally:
            cursor.close()


def save_media_metadata(record: dict[str, Any]) -> None:
    """
    Save or update media metadata.
    
    Args:
        record: Media metadata dictionary (must include unique_id)
    """
    unique_id = record.get("unique_id")
    if not unique_id or not str(unique_id).strip():
        raise ValueError("unique_id is required and cannot be empty")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO media_metadata (
                    unique_id, kind, description, status, duration, mime_type,
                    media_file, sticker_set_name, sticker_name, is_emoji_set,
                    sticker_set_title, description_retry_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    kind = VALUES(kind),
                    description = VALUES(description),
                    status = VALUES(status),
                    duration = VALUES(duration),
                    mime_type = VALUES(mime_type),
                    media_file = VALUES(media_file),
                    sticker_set_name = VALUES(sticker_set_name),
                    sticker_name = VALUES(sticker_name),
                    is_emoji_set = VALUES(is_emoji_set),
                    sticker_set_title = VALUES(sticker_set_title),
                    description_retry_count = VALUES(description_retry_count)
                """,
                (
                    unique_id,
                    record.get("kind"),
                    record.get("description"),
                    record.get("status"),
                    record.get("duration"),
                    record.get("mime_type"),
                    record.get("media_file"),
                    record.get("sticker_set_name"),
                    record.get("sticker_name"),
                    record.get("is_emoji_set"),
                    record.get("sticker_set_title"),
                    record.get("description_retry_count", 0),
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save media metadata {unique_id}: {e}")
            raise
        finally:
            cursor.close()


def update_sticker_set_metadata(
    unique_id: str,
    sticker_set_name: str | None,
    sticker_set_title: str | None = None,
) -> None:
    """
    Update sticker set metadata only when currently missing.
    Used to patch records that were cached without sticker set info.

    Args:
        unique_id: Media unique ID
        sticker_set_name: Sticker set short name to set
        sticker_set_title: Sticker set title to set (optional)
    """
    if not unique_id or not str(unique_id).strip():
        raise ValueError("unique_id is required and cannot be empty")
    if not sticker_set_name or not str(sticker_set_name).strip():
        return  # Nothing to update

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE media_metadata
                SET sticker_set_name = %s,
                    sticker_set_title = COALESCE(%s, sticker_set_title)
                WHERE unique_id = %s
                  AND (sticker_set_name IS NULL OR sticker_set_name = '')
                """,
                (sticker_set_name, sticker_set_title, unique_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.debug(
                    f"Patched sticker_set_name for {unique_id}: {sticker_set_name}"
                )
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update sticker set metadata for {unique_id}: {e}")
            raise
        finally:
            cursor.close()


def update_media_last_used(unique_id: str) -> None:
    """
    Update the last_used_at timestamp for media metadata.
    Called when media is used (e.g. in prompts, inject_media_descriptions).
    Does not include viewing in the media editor.

    Args:
        unique_id: Media unique ID to update
    """
    if not unique_id or not str(unique_id).strip():
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE media_metadata
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE unique_id = %s
                """,
                (unique_id,),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.debug(f"Failed to update last_used_at for {unique_id}: {e}")
        finally:
            cursor.close()


def delete_media_metadata(unique_id: str) -> None:
    """
    Delete media metadata.
    
    Args:
        unique_id: Media unique ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM media_metadata WHERE unique_id = %s", (unique_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete media metadata {unique_id}: {e}")
            raise
        finally:
            cursor.close()

