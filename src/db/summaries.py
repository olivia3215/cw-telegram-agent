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
                       first_message_date, last_message_date, created
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
                
                # Always include date fields, converting datetime objects to YYYY-MM-DD format
                # HTML date inputs need YYYY-MM-DD format exactly
                from datetime import datetime, date
                
                first_date = row["first_message_date"]
                if first_date is None:
                    summary["first_message_date"] = None
                elif isinstance(first_date, datetime):
                    # It's a datetime object - extract just the date part
                    summary["first_message_date"] = first_date.date().isoformat()
                elif isinstance(first_date, date):
                    # It's a date object
                    summary["first_message_date"] = first_date.isoformat()
                else:
                    # It's a string - try to extract date part
                    date_str = str(first_date).strip()
                    if not date_str:
                        summary["first_message_date"] = None
                    else:
                        # Extract YYYY-MM-DD from various formats
                        if "T" in date_str:
                            date_str = date_str.split("T")[0]
                        elif " " in date_str:
                            date_str = date_str.split(" ")[0]
                        summary["first_message_date"] = date_str
                
                last_date = row["last_message_date"]
                if last_date is None:
                    summary["last_message_date"] = None
                elif isinstance(last_date, datetime):
                    # It's a datetime object - extract just the date part
                    summary["last_message_date"] = last_date.date().isoformat()
                elif isinstance(last_date, date):
                    # It's a date object
                    summary["last_message_date"] = last_date.isoformat()
                else:
                    # It's a string - try to extract date part
                    date_str = str(last_date).strip()
                    if not date_str:
                        summary["last_message_date"] = None
                    else:
                        # Extract YYYY-MM-DD from various formats
                        if "T" in date_str:
                            date_str = date_str.split("T")[0]
                        elif " " in date_str:
                            date_str = date_str.split(" ")[0]
                        summary["last_message_date"] = date_str
                
                # Always include created field, converting datetime objects to ISO format
                created_value = row["created"]
                if created_value is None:
                    summary["created"] = None
                elif isinstance(created_value, datetime):
                    # It's a datetime object - convert to ISO format
                    summary["created"] = created_value.isoformat()
                elif isinstance(created_value, date):
                    # It's a date object - convert to ISO format (with time component as midnight)
                    summary["created"] = datetime.combine(created_value, datetime.min.time()).isoformat()
                else:
                    # It's already a string or other type - convert to string
                    summary["created"] = str(created_value) if created_value else None
                
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
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
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
                    last_message_date, created
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    min_message_id = VALUES(min_message_id),
                    max_message_id = VALUES(max_message_id),
                    first_message_date = VALUES(first_message_date),
                    last_message_date = VALUES(last_message_date),
                    created = VALUES(created)
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


def has_summaries_for_channels(agent_telegram_id: int, channel_ids: list[int]) -> set[int]:
    """
    Check which channels have summaries for a given agent (bulk query).
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_ids: List of channel IDs to check
        
    Returns:
        Set of channel IDs that have at least one summary
    """
    if not channel_ids:
        return set()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Use DISTINCT to get unique channel_ids, and IN clause for bulk query
            placeholders = ','.join(['%s'] * len(channel_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT channel_id
                FROM summaries
                WHERE agent_telegram_id = %s AND channel_id IN ({placeholders})
                """,
                (agent_telegram_id, *channel_ids),
            )
            rows = cursor.fetchall()
            return {row["channel_id"] for row in rows}
        finally:
            cursor.close()

