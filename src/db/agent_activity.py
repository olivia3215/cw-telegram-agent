# src/db/agent_activity.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for agent activity tracking.
"""

import logging
from datetime import datetime

from config import TELEGRAM_SYSTEM_USER_ID
from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def update_agent_activity(agent_telegram_id: int, channel_telegram_id: int) -> None:
    """
    Update the last send time for an agent-channel combination.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_telegram_id: The channel's Telegram ID
    """
    # Reject Telegram system user ID (777000) - should never be tracked as a conversation partner
    if channel_telegram_id == TELEGRAM_SYSTEM_USER_ID:
        logger.debug(f"Skipping agent activity update for Telegram system user {TELEGRAM_SYSTEM_USER_ID}")
        return
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO agent_activity (agent_telegram_id, channel_telegram_id, last_send_time)
                VALUES (%s, %s, NOW())
                ON DUPLICATE KEY UPDATE
                    last_send_time = NOW()
                """,
                (agent_telegram_id, channel_telegram_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(
                f"Failed to update agent activity for agent {agent_telegram_id}, channel {channel_telegram_id}: {e}"
            )
            raise
        finally:
            cursor.close()


def get_recent_activity(limit: int = 10) -> list[dict]:
    """
    Get the N most recent agent activities.
    
    Args:
        limit: Number of recent activities to return
        
    Returns:
        List of activity dictionaries with agent_telegram_id, channel_telegram_id, and last_send_time
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT agent_telegram_id, channel_telegram_id, last_send_time
                FROM agent_activity
                WHERE channel_telegram_id != %s
                ORDER BY last_send_time DESC
                LIMIT %s
                """,
                (TELEGRAM_SYSTEM_USER_ID, limit),
            )
            rows = cursor.fetchall()
            
            activities = []
            for row in rows:
                activities.append({
                    "agent_telegram_id": row["agent_telegram_id"],
                    "channel_telegram_id": row["channel_telegram_id"],
                    "last_send_time": row["last_send_time"].isoformat() if row["last_send_time"] else None,
                })
            
            return activities
        finally:
            cursor.close()


def delete_telegram_system_user_entries() -> int:
    """
    Delete all agent_activity entries for Telegram system user (777000).
    
    Returns:
        Number of rows deleted
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM agent_activity
                WHERE channel_telegram_id = %s
                """,
                (TELEGRAM_SYSTEM_USER_ID,),
            )
            deleted_count = cursor.rowcount
            conn.commit()
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} agent_activity entries for Telegram system user {TELEGRAM_SYSTEM_USER_ID}")
            return deleted_count
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete Telegram system user entries from agent_activity: {e}")
            raise
        finally:
            cursor.close()

