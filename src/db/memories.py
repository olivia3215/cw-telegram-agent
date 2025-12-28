# db/memories.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for memories.
"""

import json
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
                       creation_channel_username, metadata
                FROM memories
                WHERE agent_telegram_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id,),
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
                if row["creation_channel"]:
                    memory["creation_channel"] = row["creation_channel"]
                if row["creation_channel_id"]:
                    memory["creation_channel_id"] = row["creation_channel_id"]
                if row["creation_channel_username"]:
                    memory["creation_channel_username"] = row["creation_channel_username"]
                
                # Merge metadata JSON into memory dict
                if row["metadata"]:
                    try:
                        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        if isinstance(metadata, dict):
                            memory.update(metadata)
                    except Exception as e:
                        logger.warning(f"Failed to parse metadata JSON for memory {row['id']}: {e}")
                
                memories.append(memory)
            
            return memories
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
    metadata: dict[str, Any] | None = None,
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
        metadata: Additional metadata to store as JSON
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Extract core fields from metadata
            core_fields = {"id", "content", "created", "creation_channel", "creation_channel_id", "creation_channel_username"}
            metadata_dict = {}
            if metadata:
                for key, value in metadata.items():
                    if key not in core_fields:
                        metadata_dict[key] = value
            
            metadata_json = json.dumps(metadata_dict, ensure_ascii=False) if metadata_dict else None
            
            # Normalize datetime for MySQL
            created_normalized = normalize_datetime_for_mysql(created)
            
            cursor.execute(
                """
                INSERT INTO memories (
                    id, agent_telegram_id, content, created, creation_channel,
                    creation_channel_id, creation_channel_username, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created),
                    creation_channel = VALUES(creation_channel),
                    creation_channel_id = VALUES(creation_channel_id),
                    creation_channel_username = VALUES(creation_channel_username),
                    metadata = VALUES(metadata)
                """,
                (
                    memory_id,
                    agent_telegram_id,
                    content,
                    created_normalized,
                    creation_channel,
                    creation_channel_id,
                    creation_channel_username,
                    metadata_json,
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

