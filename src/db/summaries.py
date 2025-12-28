# db/summaries.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for summaries.
"""

import json
import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_summaries(agent_telegram_id: int, channel_id: int) -> list[dict[str, Any]]:
    """
    Load all summaries for an agent-channel combination.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        List of summary dictionaries, sorted by message ID range
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, min_message_id, max_message_id,
                       first_message_date, last_message_date, created, metadata
                FROM summaries
                WHERE agent_telegram_id = %s AND channel_id = %s
                ORDER BY min_message_id ASC, max_message_id ASC
                """,
                (agent_telegram_id, channel_id),
            )
            rows = cursor.fetchall()
            
            summaries = []
            for row in rows:
                summary = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["min_message_id"] is not None:
                    summary["min_message_id"] = row["min_message_id"]
                if row["max_message_id"] is not None:
                    summary["max_message_id"] = row["max_message_id"]
                if row["first_message_date"]:
                    summary["first_message_date"] = row["first_message_date"].isoformat()
                if row["last_message_date"]:
                    summary["last_message_date"] = row["last_message_date"].isoformat()
                if row["created"]:
                    summary["created"] = row["created"].isoformat()
                
                # Merge metadata JSON into summary dict
                if row["metadata"]:
                    try:
                        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        if isinstance(metadata, dict):
                            summary.update(metadata)
                    except Exception:
                        pass
                
                summaries.append(summary)
            
            return summaries
        finally:
            cursor.close()


def save_summary(
    agent_telegram_id: int,
    channel_id: int,
    summary_id: str,
    content: str,
    min_message_id: int | None = None,
    max_message_id: int | None = None,
    first_message_date: str | None = None,
    last_message_date: str | None = None,
    created: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Save or update a summary.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        summary_id: Unique summary ID
        content: Summary content
        min_message_id: Minimum message ID covered
        max_message_id: Maximum message ID covered
        first_message_date: First message date (ISO format string)
        last_message_date: Last message date (ISO format string)
        created: Creation timestamp (ISO format string)
        metadata: Additional metadata to store as JSON
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Extract core fields from metadata
            core_fields = {
                "id", "content", "min_message_id", "max_message_id",
                "first_message_date", "last_message_date", "created"
            }
            metadata_dict = {}
            if metadata:
                for key, value in metadata.items():
                    if key not in core_fields:
                        metadata_dict[key] = value
            
            metadata_json = json.dumps(metadata_dict, ensure_ascii=False) if metadata_dict else None
            
            # Normalize datetimes for MySQL
            first_message_date_normalized = normalize_datetime_for_mysql(first_message_date)
            last_message_date_normalized = normalize_datetime_for_mysql(last_message_date)
            created_normalized = normalize_datetime_for_mysql(created)
            
            logger.debug(
                f"Saving summary {summary_id} for agent {agent_telegram_id}, channel {channel_id}: "
                f"content_len={len(content)}, min_msg_id={min_message_id}, max_msg_id={max_message_id}, "
                f"first_date={first_message_date_normalized}, last_date={last_message_date_normalized}, "
                f"created={created_normalized}"
            )
            
            cursor.execute(
                """
                INSERT INTO summaries (
                    id, agent_telegram_id, channel_id, content,
                    min_message_id, max_message_id, first_message_date,
                    last_message_date, created, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    min_message_id = VALUES(min_message_id),
                    max_message_id = VALUES(max_message_id),
                    first_message_date = VALUES(first_message_date),
                    last_message_date = VALUES(last_message_date),
                    created = VALUES(created),
                    metadata = VALUES(metadata)
                """,
                (
                    summary_id,
                    agent_telegram_id,
                    channel_id,
                    content,
                    min_message_id,
                    max_message_id,
                    first_message_date_normalized,
                    last_message_date_normalized,
                    created_normalized,
                    metadata_json,
                ),
            )
            conn.commit()
            logger.debug(f"Successfully saved summary {summary_id} for agent {agent_telegram_id}, channel {channel_id}")
        except Exception as e:
            conn.rollback()
            logger.error(
                f"Failed to save summary {summary_id} for agent {agent_telegram_id}, channel {channel_id}: {e}. "
                f"Content length: {len(content)}, min_msg_id: {min_message_id}, max_msg_id: {max_message_id}, "
                f"first_date: {first_message_date} -> {first_message_date_normalized}, "
                f"last_date: {last_message_date} -> {last_message_date_normalized}, "
                f"created: {created} -> {created_normalized}"
            )
            raise
        finally:
            cursor.close()


def delete_summary(agent_telegram_id: int, channel_id: int, summary_id: str) -> None:
    """
    Delete a summary.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        summary_id: Summary ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM summaries WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s",
                (summary_id, agent_telegram_id, channel_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete summary {summary_id}: {e}")
            raise
        finally:
            cursor.close()

