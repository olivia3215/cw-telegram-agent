# src/db/task_log.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Task execution log database operations.
Logs all task executions with timestamps, action kinds, and details.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from clock import clock
from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def log_task_execution(
    agent_telegram_id: int,
    channel_telegram_id: int,
    action_kind: str,
    action_details: Optional[str] = None,
    failure_message: Optional[str] = None,
    task_identifier: Optional[str] = None,
) -> None:
    """
    Log a task execution to the database.

    Args:
        agent_telegram_id: The Telegram ID of the agent
        channel_telegram_id: The Telegram ID of the channel/user
        action_kind: The type of action (e.g., 'send', 'think', 'react')
        action_details: Optional JSON string or text with action details
        failure_message: Optional error message if the task failed
        task_identifier: Optional task identifier (e.g., task.id)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO task_execution_log
                (timestamp, agent_telegram_id, channel_telegram_id, action_kind, task_identifier, action_details, failure_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    clock.now(UTC),
                    agent_telegram_id,
                    channel_telegram_id,
                    action_kind,
                    task_identifier,
                    action_details,
                    failure_message,
                ),
            )
            conn.commit()
            cursor.close()
    except Exception as e:
        logger.error(f"Failed to log task execution: {e}")
        # Don't raise - logging failures shouldn't break task execution


def get_task_logs(
    agent_telegram_id: int,
    channel_telegram_id: int,
    days: int = 7,
) -> list[dict[str, Any]]:
    """
    Get task execution logs for a conversation from the past N days.

    Args:
        agent_telegram_id: The Telegram ID of the agent
        channel_telegram_id: The Telegram ID of the channel/user
        days: Number of days to look back (default: 7)

    Returns:
        List of log entries as dictionaries with keys:
        - id: Log entry ID
        - timestamp: ISO format datetime string
        - action_kind: Type of action
        - task_identifier: Task identifier (if available)
        - action_details: Details string (may be JSON)
        - failure_message: Error message if failed, None otherwise
    """
    try:
        cutoff_time = clock.now(UTC) - timedelta(days=days)
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, action_kind, task_identifier, action_details, failure_message
                FROM task_execution_log
                WHERE agent_telegram_id = %s
                  AND channel_telegram_id = %s
                  AND timestamp >= %s
                ORDER BY timestamp DESC
                """,
                (agent_telegram_id, channel_telegram_id, cutoff_time),
            )
            
            rows = cursor.fetchall()
            cursor.close()
            
            # Convert to list of dicts with ISO format timestamps
            logs = []
            for row in rows:
                # Ensure timestamp is timezone-aware (treat as UTC)
                timestamp = row["timestamp"]
                if timestamp and timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                
                logs.append({
                    "id": row["id"],
                    "timestamp": timestamp.isoformat() if timestamp else None,
                    "action_kind": row["action_kind"],
                    "task_identifier": row.get("task_identifier"),
                    "action_details": row["action_details"],
                    "failure_message": row["failure_message"],
                })
            
            return logs
    except Exception as e:
        logger.error(f"Failed to get task logs: {e}")
        return []


def get_logs_after_timestamp(
    agent_telegram_id: int,
    channel_telegram_id: int,
    after_timestamp: datetime,
) -> list[dict[str, Any]]:
    """
    Get task execution logs after a specific timestamp (for interleaving with messages).

    Args:
        agent_telegram_id: The Telegram ID of the agent
        channel_telegram_id: The Telegram ID of the channel/user
        after_timestamp: Only return logs after this time

    Returns:
        List of log entries (same format as get_task_logs)
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp, action_kind, task_identifier, action_details, failure_message
                FROM task_execution_log
                WHERE agent_telegram_id = %s
                  AND channel_telegram_id = %s
                  AND timestamp >= %s
                ORDER BY timestamp ASC
                """,
                (agent_telegram_id, channel_telegram_id, after_timestamp),
            )
            
            rows = cursor.fetchall()
            cursor.close()
            
            logs = []
            for row in rows:
                # Ensure timestamp is timezone-aware (treat as UTC)
                timestamp = row["timestamp"]
                if timestamp and timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                
                logs.append({
                    "id": row["id"],
                    "timestamp": timestamp.isoformat() if timestamp else None,
                    "action_kind": row["action_kind"],
                    "task_identifier": row.get("task_identifier"),
                    "action_details": row["action_details"],
                    "failure_message": row["failure_message"],
                })
            
            return logs
    except Exception as e:
        logger.error(f"Failed to get logs after timestamp: {e}")
        return []


def delete_old_logs(days: int = 14) -> int:
    """
    Delete task execution logs older than N days.

    Args:
        days: Delete logs older than this many days (default: 14)

    Returns:
        Number of rows deleted
    """
    try:
        cutoff_time = clock.now(UTC) - timedelta(days=days)
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM task_execution_log
                WHERE timestamp < %s
                """,
                (cutoff_time,),
            )
            deleted_count = cursor.rowcount
            conn.commit()
            cursor.close()
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} old task log entries (older than {days} days)")
            
            return deleted_count
    except Exception as e:
        logger.error(f"Failed to delete old task logs: {e}")
        return 0


def format_action_details(action_kind: str, params: dict) -> str:
    """
    Format action details based on action kind for storage.
    Returns a JSON string with all task parameters except blacklisted ones.

    Args:
        action_kind: The type of action
        params: Task parameters dictionary

    Returns:
        Formatted details string (JSON)
    """
    # Blacklist of parameters that are too verbose or not useful for logs
    blacklist = {
        "silent",  # Internal flag for silent operations
        "previous_retries",  # Retry count (tracked separately)
        "callout",  # Internal scheduling flag
        "bypass_gagged",  # Internal flag
        "clear_mentions",  # Internal Telegram flag
        "clear_reactions",  # Internal Telegram flag
    }
    
    # Create a copy of params, excluding blacklisted keys
    filtered_params = {
        k: v for k, v in params.items()
        if k not in blacklist
    }
    
    # Truncate long text fields to avoid bloating the database
    # Use a generous limit of 10,000 characters to preserve important content
    MAX_FIELD_LENGTH = 10000
    
    if "text" in filtered_params and isinstance(filtered_params["text"], str):
        if len(filtered_params["text"]) > MAX_FIELD_LENGTH:
            filtered_params["text"] = filtered_params["text"][:MAX_FIELD_LENGTH] + "..."
    
    if "content" in filtered_params and isinstance(filtered_params["content"], str):
        if len(filtered_params["content"]) > MAX_FIELD_LENGTH:
            filtered_params["content"] = filtered_params["content"][:MAX_FIELD_LENGTH] + "..."
    
    if "xsend_intent" in filtered_params and isinstance(filtered_params["xsend_intent"], str):
        if len(filtered_params["xsend_intent"]) > MAX_FIELD_LENGTH:
            filtered_params["xsend_intent"] = filtered_params["xsend_intent"][:MAX_FIELD_LENGTH] + "..."
    
    if "caption" in filtered_params and isinstance(filtered_params["caption"], str):
        if len(filtered_params["caption"]) > MAX_FIELD_LENGTH:
            filtered_params["caption"] = filtered_params["caption"][:MAX_FIELD_LENGTH] + "..."
    
    # Return empty dict if nothing to log
    if not filtered_params:
        return json.dumps({"action": action_kind})
    
    return json.dumps(filtered_params)


def _parse_cost_value(action_details: Any) -> float | None:
    """
    Parse cost from action_details payload.

    Accepts either:
    - JSON string containing {"cost": "$0.0123"} or {"cost": 0.0123}
    - dict containing the same shape
    """
    details_obj: dict[str, Any] | None = None

    if isinstance(action_details, dict):
        details_obj = action_details
    elif isinstance(action_details, str):
        try:
            parsed = json.loads(action_details)
            if isinstance(parsed, dict):
                details_obj = parsed
        except Exception:
            return None

    if not details_obj:
        return None

    raw_cost = details_obj.get("cost")
    if raw_cost is None:
        return None

    if isinstance(raw_cost, (int, float)):
        return float(raw_cost)

    if isinstance(raw_cost, str):
        normalized = raw_cost.strip()
        if normalized.startswith("$"):
            normalized = normalized[1:]
        try:
            return float(normalized)
        except ValueError:
            return None

    return None


def _build_cost_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a task_execution_log row into a normalized cost entry."""
    timestamp = row.get("timestamp")
    if timestamp and timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    action_details = row.get("action_details")
    details_obj: dict[str, Any] = {}
    if isinstance(action_details, dict):
        details_obj = action_details
    elif isinstance(action_details, str):
        try:
            parsed = json.loads(action_details)
            if isinstance(parsed, dict):
                details_obj = parsed
        except Exception:
            details_obj = {}

    cost = _parse_cost_value(action_details)

    return {
        "id": row.get("id"),
        "timestamp": timestamp.isoformat() if timestamp else None,
        "agent_telegram_id": row.get("agent_telegram_id"),
        "channel_telegram_id": row.get("channel_telegram_id"),
        "task_identifier": row.get("task_identifier"),
        "operation": details_obj.get("operation"),
        "model_name": details_obj.get("model_name"),
        "input_tokens": details_obj.get("input_tokens"),
        "output_tokens": details_obj.get("output_tokens"),
        "cost": cost,
    }


def _get_cost_logs_with_filter(where_clause: str, params: tuple[Any, ...]) -> dict[str, Any]:
    """
    Fetch llm_usage logs with a custom WHERE clause and compute total weekly cost.

    Returns:
        {
            "logs": [...],
            "total_cost": float
        }
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, timestamp, agent_telegram_id, channel_telegram_id, task_identifier, action_details
                FROM task_execution_log
                WHERE action_kind = 'llm_usage'
                  AND {where_clause}
                ORDER BY timestamp DESC
                """,
                params,
            )
            rows = cursor.fetchall()
            cursor.close()

        logs = []
        total_cost = 0.0
        for row in rows:
            entry = _build_cost_entry(row)
            logs.append(entry)
            if entry["cost"] is not None:
                total_cost += entry["cost"]

        return {
            "logs": logs,
            "total_cost": total_cost,
        }
    except Exception as e:
        logger.error(f"Failed to fetch cost logs: {e}")
        return {
            "logs": [],
            "total_cost": 0.0,
        }


def get_conversation_cost_logs(
    agent_telegram_id: int,
    channel_telegram_id: int,
    days: int = 7,
) -> dict[str, Any]:
    """Get llm_usage cost logs for one conversation from the past N days."""
    cutoff_time = clock.now(UTC) - timedelta(days=days)
    return _get_cost_logs_with_filter(
        "agent_telegram_id = %s AND channel_telegram_id = %s AND timestamp >= %s",
        (agent_telegram_id, channel_telegram_id, cutoff_time),
    )


def get_agent_cost_logs(agent_telegram_id: int, days: int = 7) -> dict[str, Any]:
    """Get llm_usage cost logs for one agent across all conversations from the past N days."""
    cutoff_time = clock.now(UTC) - timedelta(days=days)
    return _get_cost_logs_with_filter(
        "agent_telegram_id = %s AND timestamp >= %s",
        (agent_telegram_id, cutoff_time),
    )


def get_global_cost_logs(days: int = 7) -> dict[str, Any]:
    """Get llm_usage cost logs globally from the past N days."""
    cutoff_time = clock.now(UTC) - timedelta(days=days)
    return _get_cost_logs_with_filter(
        "timestamp >= %s",
        (cutoff_time,),
    )
