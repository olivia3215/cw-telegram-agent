# db/media_metadata.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for media metadata.
"""

import json
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
                       sticker_set_title, metadata
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
            
            # Merge metadata JSON into record
            if row["metadata"]:
                try:
                    metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                    if isinstance(metadata, dict):
                        record.update(metadata)
                except Exception:
                    pass
            
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
    if not unique_id:
        raise ValueError("unique_id is required")
    
    # Extract core fields
    core_fields = {
        "unique_id", "kind", "description", "status", "duration", "mime_type",
        "media_file", "sticker_set_name", "sticker_name", "is_emoji_set", "sticker_set_title"
    }
    
    metadata_dict = {}
    for key, value in record.items():
        if key not in core_fields:
            metadata_dict[key] = value
    
    metadata_json = json.dumps(metadata_dict, ensure_ascii=False) if metadata_dict else None
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO media_metadata (
                    unique_id, kind, description, status, duration, mime_type,
                    media_file, sticker_set_name, sticker_name, is_emoji_set,
                    sticker_set_title, metadata
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
                    metadata = VALUES(metadata)
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
                    metadata_json,
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save media metadata {unique_id}: {e}")
            raise
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

