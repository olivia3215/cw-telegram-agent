# src/db/schedules.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for schedules.
"""

import json
import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_schedule(agent_telegram_id: int) -> dict[str, Any] | None:
    """
    Load an agent's schedule.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        
    Returns:
        Schedule dictionary or None if not found
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT timezone, last_extended, activities
                FROM schedules
                WHERE agent_telegram_id = %s
                """,
                (agent_telegram_id,),
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            schedule = {
                "activities": json.loads(row["activities"]) if isinstance(row["activities"], str) else row["activities"],
            }
            
            if row["timezone"]:
                schedule["timezone"] = row["timezone"]
            if row["last_extended"]:
                schedule["last_extended"] = row["last_extended"].isoformat()
            
            return schedule
        finally:
            cursor.close()


def save_schedule(agent_telegram_id: int, schedule: dict[str, Any]) -> None:
    """
    Save an agent's schedule.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        schedule: Schedule dictionary
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            activities = schedule.get("activities", [])
            if not isinstance(activities, list):
                raise ValueError(f"activities must be a list, got {type(activities).__name__}")
            activities_json = json.dumps(activities, ensure_ascii=False)
            
            timezone = schedule.get("timezone")
            last_extended = schedule.get("last_extended")
            
            # Normalize datetime for MySQL
            last_extended_normalized = normalize_datetime_for_mysql(last_extended)
            
            cursor.execute(
                """
                INSERT INTO schedules (agent_telegram_id, timezone, last_extended, activities)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    timezone = VALUES(timezone),
                    last_extended = VALUES(last_extended),
                    activities = VALUES(activities)
                """,
                (agent_telegram_id, timezone, last_extended_normalized, activities_json),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save schedule for agent {agent_telegram_id}: {e}")
            raise
        finally:
            cursor.close()

