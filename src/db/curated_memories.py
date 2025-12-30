# db/curated_memories.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for curated memories.
"""

import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_curated_memories(agent_telegram_id: int, channel_id: int) -> list[dict[str, Any]]:
    """
    Load curated memories for an agent and channel.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        List of curated memory dictionaries
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, created
                FROM curated_memories
                WHERE agent_telegram_id = %s AND channel_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id, channel_id),
            )
            rows = cursor.fetchall()
            
            memories = []
            for row in rows:
                memory = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["created"]:
                    memory["created"] = row["created"].isoformat()
                
                memories.append(memory)
            
            return memories
        finally:
            cursor.close()


def save_curated_memory(
    agent_telegram_id: int,
    channel_id: int,
    memory_id: str,
    content: str,
    created: str | None = None,
) -> None:
    """
    Save or update a curated memory.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        memory_id: Unique memory ID
        content: Memory content
        created: Creation timestamp (ISO format string)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Normalize datetime for MySQL
            created_normalized = normalize_datetime_for_mysql(created)
            
            cursor.execute(
                """
                INSERT INTO curated_memories (
                    id, agent_telegram_id, channel_id, content, created
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created)
                """,
                (
                    memory_id,
                    agent_telegram_id,
                    channel_id,
                    content,
                    created_normalized,
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save curated memory {memory_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_curated_memory(agent_telegram_id: int, channel_id: int, memory_id: str) -> None:
    """
    Delete a curated memory.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        memory_id: Memory ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM curated_memories WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s",
                (memory_id, agent_telegram_id, channel_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete curated memory {memory_id}: {e}")
            raise
        finally:
            cursor.close()


def agents_with_curated_memories(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have curated memories (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one curated memory
    """
    if not agent_telegram_ids:
        return set()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Use DISTINCT to get unique agent_telegram_ids, and IN clause for bulk query
            placeholders = ','.join(['%s'] * len(agent_telegram_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT agent_telegram_id
                FROM curated_memories
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            return {row["agent_telegram_id"] for row in rows}
        finally:
            cursor.close()

