# src/handlers/event.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Handler for event tasks: create, update, or delete scheduled actions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from db import events as db_events
from handlers.registry import register_immediate_task_handler
from task_graph import TaskNode
from utils import coerce_to_str
from utils.formatting import format_log_prefix, format_log_prefix_resolved

logger = logging.getLogger(__name__)


def _parse_time_to_utc(time_str: str, tz: ZoneInfo, *, explicit_timezone: bool = False) -> datetime | None:
    """
    Parse ISO-ish time string to UTC.
    If the string has no timezone offset, interpret in the given tz.
    If the string has an offset (e.g. Z) and explicit_timezone is True (caller set timezone),
    the timezone takes priority: treat the datetime as zone-naive in that zone, then convert to UTC.
    """
    if not time_str or not isinstance(time_str, str):
        return None
    s = time_str.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None and explicit_timezone:
        dt_naive = dt.replace(tzinfo=None)
        return dt_naive.replace(tzinfo=tz).astimezone(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


@register_immediate_task_handler("event")
async def handle_immediate_event(task: TaskNode, *, agent, channel_id: int) -> bool:
    if agent is None:
        logger.warning("[event] Missing agent context; deferring event task")
        return False
    if not getattr(agent, "is_authenticated", False) or not getattr(agent, "agent_id", None):
        log_prefix = await format_log_prefix(agent.name, None)
        logger.warning(f"{log_prefix} Cannot process event task: agent not authenticated")
        return False

    params = dict(task.params or {})
    params.pop("kind", None)
    event_id = params.pop("id", None) or f"event-{uuid.uuid4().hex[:8]}"
    intent_raw = params.pop("intent", None)
    intent = (coerce_to_str(intent_raw).strip() if intent_raw is not None else "") or ""
    time_str = params.pop("time", None)
    timezone_str = (params.pop("timezone", None) or "").strip() or None
    interval = params.pop("interval", None)
    occurrences = params.pop("occurrences", None)

    agent_id = agent.agent_id
    log_prefix = await format_log_prefix(agent.name, None)

    if not intent and event_id:
        try:
            db_events.delete_event(agent_id, channel_id, event_id)
            logger.info(f"{log_prefix} Deleted event {event_id}")
        except Exception as e:
            logger.exception(f"{log_prefix} Failed to delete event {event_id}: {e}")
        return True

    if not intent or time_str is None:
        logger.warning(f"{log_prefix} Event task requires intent and time for create/update")
        return True

    tz = agent.timezone
    if timezone_str:
        try:
            tz = ZoneInfo(timezone_str)
        except Exception as e:
            logger.warning(f"{log_prefix} Invalid timezone {timezone_str}: {e}")

    time_utc = _parse_time_to_utc(coerce_to_str(time_str), tz, explicit_timezone=bool(timezone_str))
    if time_utc is None:
        logger.warning(f"{log_prefix} Could not parse event time: {time_str}")
        return True

    if occurrences is not None:
        try:
            occurrences = int(occurrences)
            if occurrences < 1:
                occurrences = None
        except (TypeError, ValueError):
            occurrences = None

    try:
        db_events.save_event(
            agent_telegram_id=agent_id,
            channel_id=channel_id,
            event_id=event_id,
            time_utc=time_utc,
            intent=intent,
            interval_value=interval if interval else None,
            occurrences=occurrences,
        )
        logger.info(f"{log_prefix} Saved event {event_id} at {time_utc.isoformat()}")
    except Exception as e:
        logger.exception(f"{log_prefix} Failed to save event {event_id}: {e}")
    return True
