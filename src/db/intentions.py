# db/intentions.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for intentions.
"""

import json
import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_intentions(agent_telegram_id: int) -> list[dict[str, Any]]:
    """
    Load all intentions for an agent.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        
    Returns:
        List of intention dictionaries
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, created, metadata
                FROM intentions
                WHERE agent_telegram_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id,),
            )
            rows = cursor.fetchall()
            
            intentions = []
            for row in rows:
                intention = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["created"]:
                    intention["created"] = row["created"].isoformat()
                
                # Merge metadata JSON into intention dict
                if row["metadata"]:
                    try:
                        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        if isinstance(metadata, dict):
                            intention.update(metadata)
                    except Exception as e:
                        logger.warning(f"Failed to parse metadata JSON for intention {row['id']}: {e}")
                
                intentions.append(intention)
            
            return intentions
        finally:
            cursor.close()


def save_intention(
    agent_telegram_id: int,
    intention_id: str,
    content: str,
    created: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Save or update an intention.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        intention_id: Unique intention ID
        content: Intention content
        created: Creation timestamp (ISO format string)
        metadata: Additional metadata to store as JSON
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Extract core fields from metadata
            core_fields = {"id", "content", "created"}
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
                INSERT INTO intentions (id, agent_telegram_id, content, created, metadata)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created),
                    metadata = VALUES(metadata)
                """,
                (intention_id, agent_telegram_id, content, created_normalized, metadata_json),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save intention {intention_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_intention(agent_telegram_id: int, intention_id: str) -> None:
    """
    Delete an intention.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        intention_id: Intention ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM intentions WHERE id = %s AND agent_telegram_id = %s",
                (intention_id, agent_telegram_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete intention {intention_id}: {e}")
            raise
        finally:
            cursor.close()


def agents_with_intentions(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have intentions (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one intention
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
                FROM intentions
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            return {row["agent_telegram_id"] for row in rows}
        finally:
            cursor.close()

