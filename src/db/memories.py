# src/db/memories.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for memories.
"""

import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_memories(agent_telegram_id: int) -> list[dict[str, Any]]:
    """
    Load all memories for an agent.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        
    Returns:
        List of memory dictionaries
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, created, creation_channel, creation_channel_id,
                       creation_channel_username
                FROM memories
                WHERE agent_telegram_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id,),
            )
            rows = cursor.fetchall()
            
            # Commit the read transaction to ensure fresh data on next read
            # This prevents stale reads when connections are reused from the pool
            conn.commit()
            
            memories = []
            for row in rows:
                memory = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["created"]:
                    memory["created"] = row["created"].isoformat()
                if row["creation_channel"]:
                    memory["creation_channel"] = row["creation_channel"]
                if row["creation_channel_id"]:
                    memory["creation_channel_id"] = row["creation_channel_id"]
                if row["creation_channel_username"]:
                    memory["creation_channel_username"] = row["creation_channel_username"]
                
                memories.append(memory)
            
            return memories
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to load memories: {e}")
            raise
        finally:
            cursor.close()


def save_memory(
    agent_telegram_id: int,
    memory_id: str,
    content: str,
    created: str | None = None,
    creation_channel: str | None = None,
    creation_channel_id: int | None = None,
    creation_channel_username: str | None = None,
) -> None:
    """
    Save or update a memory.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        memory_id: Unique memory ID
        content: Memory content
        created: Creation timestamp (ISO format string)
        creation_channel: Channel name where memory was created
        creation_channel_id: Channel ID where memory was created
        creation_channel_username: Channel username where memory was created
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Normalize datetime for MySQL
            created_normalized = normalize_datetime_for_mysql(created)
            
            cursor.execute(
                """
                INSERT INTO memories (
                    id, agent_telegram_id, content, created, creation_channel,
                    creation_channel_id, creation_channel_username
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created),
                    creation_channel = VALUES(creation_channel),
                    creation_channel_id = VALUES(creation_channel_id),
                    creation_channel_username = VALUES(creation_channel_username)
                """,
                (
                    memory_id,
                    agent_telegram_id,
                    content,
                    created_normalized,
                    creation_channel,
                    creation_channel_id,
                    creation_channel_username,
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save memory {memory_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_memory(agent_telegram_id: int, memory_id: str) -> None:
    """
    Delete a memory.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        memory_id: Memory ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM memories WHERE id = %s AND agent_telegram_id = %s",
                (memory_id, agent_telegram_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise
        finally:
            cursor.close()


def agents_with_memories(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have memories (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one memory
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
                FROM memories
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            # Commit the read transaction to ensure fresh data on next read
            conn.commit()
            return {row["agent_telegram_id"] for row in rows}
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to check agents with memories: {e}")
            raise
        finally:
            cursor.close()

