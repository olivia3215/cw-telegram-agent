# src/db/events.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for events (scheduled actions).
"""

import re
import logging
from datetime import datetime
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)

# Interval: "N unit" with unit in minute(s), hour(s), day(s), week(s). Store as plural.
_INTERVAL_PLURAL = {"minute": "minutes", "minutes": "minutes", "hour": "hours", "hours": "hours", "day": "days", "days": "days", "week": "weeks", "weeks": "weeks"}
_INTERVAL_PATTERN = re.compile(r"^\s*([\d.]+)\s+(minute|minutes|hour|hours|day|days|week|weeks)\s*$", re.IGNORECASE)


def normalize_interval_to_plural(interval_str: str | None) -> str | None:
    """
    Normalize an interval string to plural form (e.g. "1 hour" -> "1 hours").
    Accepts singular or plural; returns plural. Returns None for empty/invalid.
    """
    if not interval_str or not isinstance(interval_str, str):
        return None
    s = interval_str.strip()
    if not s:
        return None
    m = _INTERVAL_PATTERN.match(s)
    if not m:
        return None
    num, unit = m.group(1), m.group(2).lower()
    plural = _INTERVAL_PLURAL.get(unit)
    if not plural:
        return None
    return f"{num} {plural}"


def parse_interval_seconds(interval_value: str | None) -> float | None:
    """
    Parse stored interval string (e.g. "1 hours", "30 minutes") into seconds.
    Returns None if absent or invalid.
    """
    if not interval_value or not isinstance(interval_value, str):
        return None
    s = interval_value.strip()
    if not s:
        return None
    m = _INTERVAL_PATTERN.match(s)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2).lower()
    if unit in ("minute", "minutes"):
        return num * 60
    if unit in ("hour", "hours"):
        return num * 3600
    if unit in ("day", "days"):
        return num * 86400
    if unit in ("week", "weeks"):
        return num * 604800
    return None


def load_events(agent_telegram_id: int, channel_id: int) -> list[dict[str, Any]]:
    """
    Load all events for an agent-channel combination, ordered by time_utc.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, time_utc, intent, interval_value, occurrences
                FROM events
                WHERE agent_telegram_id = %s AND channel_id = %s
                ORDER BY time_utc ASC
                """,
                (agent_telegram_id, channel_id),
            )
            rows = cursor.fetchall()
            conn.commit()
            out = []
            for row in rows:
                ev = {
                    "id": row["id"],
                    "intent": row["intent"] or "",
                }
                if row["time_utc"]:
                    ev["time_utc"] = row["time_utc"].isoformat()
                if row.get("interval_value") is not None:
                    ev["interval"] = row["interval_value"]
                if row.get("occurrences") is not None:
                    ev["occurrences"] = int(row["occurrences"])
                out.append(ev)
            return out
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to load events: {e}")
            raise
        finally:
            cursor.close()


def save_event(
    agent_telegram_id: int,
    channel_id: int,
    event_id: str,
    time_utc: datetime | str,
    intent: str,
    interval_value: str | None = None,
    occurrences: int | None = None,
) -> None:
    """
    Insert or replace an event. time_utc can be datetime or ISO string.
    interval_value is normalized to plural form on write.
    """
    norm_interval = normalize_interval_to_plural(interval_value) if interval_value else None
    if isinstance(time_utc, datetime):
        time_str = time_utc.strftime("%Y-%m-%d %H:%M:%S")
    else:
        time_str = normalize_datetime_for_mysql(time_utc)
    if not time_str:
        raise ValueError("Event time_utc is required")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO events (id, agent_telegram_id, channel_id, time_utc, intent, interval_value, occurrences)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    time_utc = VALUES(time_utc),
                    intent = VALUES(intent),
                    interval_value = VALUES(interval_value),
                    occurrences = VALUES(occurrences)
                """,
                (event_id, agent_telegram_id, channel_id, time_str, intent, norm_interval, occurrences),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save event {event_id}: {e}")
            raise
        finally:
            cursor.close()


def update_event_time_and_occurrences(
    agent_telegram_id: int,
    channel_id: int,
    event_id: str,
    time_utc: datetime | str,
    occurrences: int | None = None,
) -> None:
    """
    Update an event's time_utc and optionally occurrences (for rescheduling after fire).
    """
    if isinstance(time_utc, datetime):
        time_str = time_utc.strftime("%Y-%m-%d %H:%M:%S")
    else:
        time_str = normalize_datetime_for_mysql(time_utc)
    if not time_str:
        raise ValueError("Event time_utc is required")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if occurrences is not None:
                cursor.execute(
                    """
                    UPDATE events
                    SET time_utc = %s, occurrences = %s
                    WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s
                    """,
                    (time_str, occurrences, event_id, agent_telegram_id, channel_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE events
                    SET time_utc = %s
                    WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s
                    """,
                    (time_str, event_id, agent_telegram_id, channel_id),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update event {event_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_event(agent_telegram_id: int, channel_id: int, event_id: str) -> None:
    """Delete an event by id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM events WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s",
                (event_id, agent_telegram_id, channel_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete event {event_id}: {e}")
            raise
        finally:
            cursor.close()


def get_next_events_ordered(limit: int = 50) -> list[dict[str, Any]]:
    """
    Return the next events ordered by time_utc ASC, up to limit.
    Caller must filter by non-gagged (agent, channel); first non-gagged is the one to fire.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, agent_telegram_id, channel_id, time_utc, intent, interval_value, occurrences
                FROM events
                ORDER BY time_utc ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            conn.commit()
            out = []
            for row in rows:
                ev = {
                    "id": row["id"],
                    "agent_telegram_id": int(row["agent_telegram_id"]),
                    "channel_id": int(row["channel_id"]),
                    "intent": row["intent"] or "",
                    "interval_value": row.get("interval_value"),
                    "occurrences": int(row["occurrences"]) if row.get("occurrences") is not None else None,
                }
                if row["time_utc"]:
                    ev["time_utc"] = row["time_utc"]  # keep as datetime for comparison
                out.append(ev)
            return out
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to get next events: {e}")
            raise
        finally:
            cursor.close()


def has_events_for_agent(agent_telegram_id: int) -> bool:
    """True if the agent has at least one event in any channel."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE agent_telegram_id = %s LIMIT 1",
                (agent_telegram_id,),
            )
            row = cursor.fetchone()
            return (row and row["cnt"] > 0) or False
        finally:
            cursor.close()


def channels_with_events(agent_telegram_id: int) -> set[int]:
    """Return set of channel_ids that have at least one event for this agent."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT DISTINCT channel_id FROM events WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            rows = cursor.fetchall()
            return {int(r["channel_id"]) for r in rows}
        finally:
            cursor.close()
