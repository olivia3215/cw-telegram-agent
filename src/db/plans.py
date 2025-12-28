# db/plans.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for plans.
"""

import json
import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_plans(agent_telegram_id: int, channel_id: int) -> list[dict[str, Any]]:
    """
    Load all plans for an agent-channel combination.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        List of plan dictionaries
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, created, metadata
                FROM plans
                WHERE agent_telegram_id = %s AND channel_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id, channel_id),
            )
            rows = cursor.fetchall()
            
            plans = []
            for row in rows:
                plan = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["created"]:
                    plan["created"] = row["created"].isoformat()
                
                # Merge metadata JSON into plan dict
                if row["metadata"]:
                    try:
                        metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        if isinstance(metadata, dict):
                            plan.update(metadata)
                    except Exception as e:
                        logger.warning(f"Failed to parse metadata JSON for plan {row['id']}: {e}")
                
                plans.append(plan)
            
            return plans
        finally:
            cursor.close()


def save_plan(
    agent_telegram_id: int,
    channel_id: int,
    plan_id: str,
    content: str,
    created: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Save or update a plan.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        plan_id: Unique plan ID
        content: Plan content
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
                INSERT INTO plans (id, agent_telegram_id, channel_id, content, created, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created),
                    metadata = VALUES(metadata)
                """,
                (plan_id, agent_telegram_id, channel_id, content, created_normalized, metadata_json),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save plan {plan_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_plan(agent_telegram_id: int, channel_id: int, plan_id: str) -> None:
    """
    Delete a plan.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        plan_id: Plan ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM plans WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s",
                (plan_id, agent_telegram_id, channel_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete plan {plan_id}: {e}")
            raise
        finally:
            cursor.close()


def has_plans_for_agent(agent_telegram_id: int) -> bool:
    """
    Check if an agent has any plans across all channels.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        
    Returns:
        True if the agent has at least one plan, False otherwise
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) as count FROM plans WHERE agent_telegram_id = %s LIMIT 1",
                (agent_telegram_id,),
            )
            row = cursor.fetchone()
            return row["count"] > 0 if row else False
        finally:
            cursor.close()

