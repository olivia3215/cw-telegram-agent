# db/plans.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for plans.
"""

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
                SELECT id, content, created
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
) -> None:
    """
    Save or update a plan.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        plan_id: Unique plan ID
        content: Plan content
        created: Creation timestamp (ISO format string)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Normalize datetime for MySQL
            created_normalized = normalize_datetime_for_mysql(created)
            
            cursor.execute(
                """
                INSERT INTO plans (id, agent_telegram_id, channel_id, content, created)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created)
                """,
                (plan_id, agent_telegram_id, channel_id, content, created_normalized),
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


def agents_with_plans(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have plans across all channels (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one plan
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
                FROM plans
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            return {row["agent_telegram_id"] for row in rows}
        finally:
            cursor.close()

