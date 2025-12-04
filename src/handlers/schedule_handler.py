# handlers/schedule_handler.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Handler for schedule tasks that allow agents to manage their daily schedules.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from handlers.registry import register_immediate_task_handler
from schedule import ScheduleActivity
from task_graph import TaskNode

logger = logging.getLogger(__name__)


def _normalize_datetime(dt: datetime) -> datetime:
    """
    Normalize a datetime to be timezone-aware.
    
    If the datetime is timezone-naive, it's assumed to be in UTC and made UTC-aware.
    This allows comparison between legacy (naive) and new (aware) datetimes.
    
    Args:
        dt: Datetime to normalize (may be naive or aware)
    
    Returns:
        Timezone-aware datetime
    """
    if dt.tzinfo is None:
        # Legacy timezone-naive datetime - assume UTC
        return dt.replace(tzinfo=UTC)
    return dt


def _activities_overlap(start1: datetime, end1: datetime, start2: datetime, end2: datetime) -> bool:
    """
    Check if two time ranges overlap.
    
    Two ranges overlap if they share any time, but NOT if they only touch at boundaries.
    For example: [9am-5pm] and [5pm-6pm] do NOT overlap (exact boundary).
    
    Args:
        start1: Start time of first range
        end1: End time of first range
        start2: Start time of second range
        end2: End time of second range
    
    Returns:
        True if ranges overlap (excluding exact boundary touches)
    """
    try:
        # Normalize datetimes to handle mixed timezone-naive/aware comparisons
        start1 = _normalize_datetime(start1)
        end1 = _normalize_datetime(end1)
        start2 = _normalize_datetime(start2)
        end2 = _normalize_datetime(end2)
        
        # Two ranges overlap if: start1 < end2 AND start2 < end1
        # This excludes exact boundary touches (e.g., one ends exactly when another starts)
        return start1 < end2 and start2 < end1
    except TypeError as e:
        # Safety net: if normalization didn't work, log and return False (no overlap)
        logger.warning(f"[schedule] TypeError comparing datetimes for overlap: {e}")
        return False


def _split_activity_for_overlap(
    existing_activity: dict, new_start: datetime, new_end: datetime
) -> list[dict]:
    """
    Split an existing activity to remove overlapping time with a new activity.
    
    Returns a list of activity segments (before and/or after the overlap).
    Segments preserve all properties of the original activity but get new IDs.
    Empty segments (start_time == end_time) are excluded.
    
    Args:
        existing_activity: Activity dictionary to split
        new_start: Start time of the new overlapping activity
        new_end: End time of the new overlapping activity
    
    Returns:
        List of activity dictionaries (0, 1, or 2 segments)
    """
    try:
        existing_start = datetime.fromisoformat(existing_activity["start_time"])
        existing_end = datetime.fromisoformat(existing_activity["end_time"])
    except (KeyError, ValueError) as e:
        logger.warning(f"[schedule] Failed to parse activity times for splitting: {e}")
        return []
    
    # Normalize datetimes to handle mixed timezone-naive/aware comparisons
    existing_start = _normalize_datetime(existing_start)
    existing_end = _normalize_datetime(existing_end)
    new_start = _normalize_datetime(new_start)
    new_end = _normalize_datetime(new_end)
    
    segments = []
    
    try:
        # Create "before" segment if there's time before the overlap
        if existing_start < new_start:
            before_segment = existing_activity.copy()
            before_segment["id"] = f"act-{uuid.uuid4().hex[:8]}"
            before_segment["start_time"] = existing_start.isoformat()
            before_segment["end_time"] = new_start.isoformat()
            segments.append(before_segment)
        
        # Create "after" segment if there's time after the overlap
        if new_end < existing_end:
            after_segment = existing_activity.copy()
            after_segment["id"] = f"act-{uuid.uuid4().hex[:8]}"
            after_segment["start_time"] = new_end.isoformat()
            after_segment["end_time"] = existing_end.isoformat()
            segments.append(after_segment)
    except TypeError as e:
        # Safety net: if comparison fails, log and return empty (no split)
        logger.warning(f"[schedule] TypeError comparing datetimes for splitting: {e}")
        return []
    
    return segments


def _handle_activity_overlaps(
    activities: list[dict], new_start: datetime, new_end: datetime, exclude_id: str | None = None
) -> list[dict]:
    """
    Handle overlaps between a new activity and existing activities.
    
    Splits overlapping activities and removes empty ones. Does not add the new activity.
    
    Args:
        activities: List of existing activity dictionaries
        new_start: Start time of the new activity
        new_end: End time of the new activity
        exclude_id: Optional activity ID to exclude from overlap checking (e.g., when updating)
    
    Returns:
        Updated list of activities with overlaps handled
    """
    result = []
    
    for act in activities:
        # Skip the activity being updated (don't check it for overlap with itself)
        if exclude_id and act.get("id") == exclude_id:
            result.append(act)
            continue
        
        try:
            act_start = datetime.fromisoformat(act["start_time"])
            act_end = datetime.fromisoformat(act["end_time"])
        except (KeyError, ValueError) as e:
            logger.warning(f"[schedule] Failed to parse activity times: {e}, skipping")
            result.append(act)  # Keep invalid activities as-is
            continue
        
        # Normalize datetimes to handle mixed timezone-naive/aware comparisons
        act_start = _normalize_datetime(act_start)
        act_end = _normalize_datetime(act_end)
        new_start_normalized = _normalize_datetime(new_start)
        new_end_normalized = _normalize_datetime(new_end)
        
        # Check if this activity overlaps with the new time range
        try:
            if _activities_overlap(act_start, act_end, new_start_normalized, new_end_normalized):
                # Split the activity and add non-empty segments
                segments = _split_activity_for_overlap(act, new_start_normalized, new_end_normalized)
                result.extend(segments)
            else:
                # No overlap, keep the activity as-is
                result.append(act)
        except TypeError as e:
            # Safety net: if overlap check fails, keep the activity as-is
            logger.warning(f"[schedule] TypeError checking overlap: {e}, keeping activity as-is")
            result.append(act)
    
    return result


def _sort_activities(activities: list[dict]) -> list[dict]:
    """
    Sort activities by start_time.
    
    Activities with invalid or missing start_time are placed at the end.
    
    Args:
        activities: List of activity dictionaries
    
    Returns:
        Sorted list of activities
    """
    def sort_key(act: dict) -> tuple[int, datetime]:
        try:
            start_str = act.get("start_time", "")
            if not start_str:
                return (1, datetime.min.replace(tzinfo=UTC))  # Invalid: sort to end (use UTC-aware)
            dt = datetime.fromisoformat(start_str)
            # Normalize to handle mixed timezone-naive/aware datetimes
            dt = _normalize_datetime(dt)
            return (0, dt)  # Valid: sort normally
        except (ValueError, KeyError, TypeError) as e:
            # Catch TypeError in case normalization or comparison fails
            logger.debug(f"[schedule] Error in sort_key for activity: {e}")
            return (1, datetime.min.replace(tzinfo=UTC))  # Invalid: sort to end (use UTC-aware)
    
    try:
        return sorted(activities, key=sort_key)
    except TypeError as e:
        # Safety net: if sorting fails due to mixed timezone awareness, log and return unsorted
        logger.warning(f"[schedule] TypeError sorting activities: {e}, returning unsorted")
        return activities


@register_immediate_task_handler("schedule")
async def handle_immediate_schedule(task: TaskNode, *, agent, channel_id: int) -> bool:
    """
    Handle schedule tasks: create, update, or delete schedule entries.
    
    Operation is determined automatically:
    - If id matches existing entry and activity_name is empty → delete
    - If id matches existing entry and activity_name is not empty → update
    - If id doesn't exist or is not provided → create
    
    Args:
        task: The schedule task node
        agent: Agent instance
        channel_id: Channel ID (not used for schedules, but required by interface)
    
    Returns:
        True if the task was handled successfully
    """
    if agent is None:
        logger.warning("[schedule] Missing agent context; deferring schedule task")
        return False
    
    if not agent.daily_schedule_description:
        logger.warning(f"[schedule] Agent {agent.name} does not have a daily schedule configured")
        return False
    
    params = task.params or {}
    activity_id = params.get("id")
    activity_name = params.get("activity_name", "")
    
    # Load existing schedule to check if ID exists
    schedule = agent._load_schedule()
    existing_ids = set()
    if schedule:
        existing_ids = {act.get("id") for act in schedule.get("activities", []) if act.get("id")}
    
    # Determine operation
    if activity_id and activity_id in existing_ids:
        # ID exists - check if it's a delete or update
        if not activity_name or activity_name.strip() == "":
            return await _handle_delete_schedule(agent, task)
        else:
            return await _handle_update_schedule(agent, task)
    else:
        # ID doesn't exist or not provided - create
        return await _handle_create_schedule(agent, task)


async def _handle_create_schedule(agent, task: TaskNode) -> bool:
    """Create a new schedule entry."""
    try:
        params = task.params or {}
        
        # Parse required fields
        start_time_str = params.get("start_time")
        end_time_str = params.get("end_time")
        activity_name = params.get("activity_name")
        responsiveness = params.get("responsiveness")
        description = params.get("description")
        
        if not all([start_time_str, end_time_str, activity_name, responsiveness is not None, description]):
            logger.warning(f"[schedule] Missing required fields for create: {params}")
            return False
        
        # Parse datetimes
        try:
            start_time = datetime.fromisoformat(start_time_str)
            end_time = datetime.fromisoformat(end_time_str)
        except ValueError as e:
            logger.warning(f"[schedule] Invalid datetime format: {e}")
            return False
        
        # Validate that datetimes are timezone-aware
        # Comparing timezone-naive with timezone-aware datetimes raises TypeError
        if start_time.tzinfo is None:
            logger.warning(
                f"[schedule] start_time must be timezone-aware (got: {start_time_str}). "
                f"ISO 8601 datetime strings must include timezone offset (e.g., '2025-12-02T06:00:00-10:00')."
            )
            return False
        if end_time.tzinfo is None:
            logger.warning(
                f"[schedule] end_time must be timezone-aware (got: {end_time_str}). "
                f"ISO 8601 datetime strings must include timezone offset (e.g., '2025-12-02T06:00:00-10:00')."
            )
            return False
        
        # Generate ID if not provided
        activity_id = params.get("id") or f"act-{uuid.uuid4().hex[:8]}"
        
        # Create activity dict
        activity_dict = {
            "id": activity_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "activity_name": activity_name,
            "responsiveness": int(responsiveness),
            "description": description,
        }
        
        # Load existing schedule
        schedule = agent._load_schedule()
        if not schedule:
            schedule = {
                "version": "1.0",
                "agent_name": agent.name,
                "timezone": str(agent.timezone),
                "last_extended": None,
                "activities": [],
            }
        
        # Handle overlaps: split existing activities that overlap with the new one
        schedule["activities"] = _handle_activity_overlaps(
            schedule["activities"], start_time, end_time
        )
        
        # Add new activity
        schedule["activities"].append(activity_dict)
        
        # Sort activities by start_time
        schedule["activities"] = _sort_activities(schedule["activities"])
        
        # Save schedule
        agent._save_schedule(schedule)
        
        logger.info(
            f"[{agent.name}] Created schedule entry: {activity_name} "
            f"({start_time.isoformat()} - {end_time.isoformat()})"
        )
        return True
        
    except Exception as e:
        logger.error(f"[schedule] Error creating schedule entry: {e}")
        return False


async def _handle_update_schedule(agent, task: TaskNode) -> bool:
    """Update an existing schedule entry."""
    try:
        params = task.params or {}
        activity_id = params.get("id")
        
        if not activity_id:
            logger.warning("[schedule] Missing id for update")
            return False
        
        # Load existing schedule
        schedule = agent._load_schedule()
        if not schedule:
            logger.warning(f"[{agent.name}] No schedule found for update")
            return False
        
        # Find the activity to update and get its current times
        activities = schedule.get("activities", [])
        found = False
        activity_index = None
        current_start = None
        current_end = None
        
        for i, act in enumerate(activities):
            if act.get("id") == activity_id:
                activity_index = i
                try:
                    current_start = datetime.fromisoformat(act.get("start_time", ""))
                    current_end = datetime.fromisoformat(act.get("end_time", ""))
                    # Normalize to handle legacy timezone-naive datetimes
                    current_start = _normalize_datetime(current_start)
                    current_end = _normalize_datetime(current_end)
                except (ValueError, KeyError, TypeError) as e:
                    logger.warning(f"[schedule] Activity {activity_id} has invalid times: {e}")
                found = True
                break
        
        if not found:
            logger.warning(f"[{agent.name}] Schedule entry {activity_id} not found for update")
            return False
        
        # Parse and validate new times if provided
        new_start = None
        new_end = None
        times_being_updated = False
        
        if "start_time" in params:
            try:
                new_start = datetime.fromisoformat(params["start_time"])
                if new_start.tzinfo is None:
                    logger.warning(
                        f"[schedule] start_time must be timezone-aware (got: {params['start_time']}). "
                        f"ISO 8601 datetime strings must include timezone offset."
                    )
                    return False
                times_being_updated = True
            except ValueError as e:
                logger.warning(f"[schedule] Invalid start_time format: {e}")
                return False
        else:
            new_start = current_start
        
        if "end_time" in params:
            try:
                new_end = datetime.fromisoformat(params["end_time"])
                if new_end.tzinfo is None:
                    logger.warning(
                        f"[schedule] end_time must be timezone-aware (got: {params['end_time']}). "
                        f"ISO 8601 datetime strings must include timezone offset."
                    )
                    return False
                times_being_updated = True
            except ValueError as e:
                logger.warning(f"[schedule] Invalid end_time format: {e}")
                return False
        else:
            new_end = current_end
        
        # If times are being updated, handle overlaps with OTHER activities
        if times_being_updated and new_start and new_end:
            # Handle overlaps: split existing activities that overlap with the new times
            # Exclude the activity being updated from overlap checking
            activities = _handle_activity_overlaps(
                activities, new_start, new_end, exclude_id=activity_id
            )
            # Find the activity again after overlap handling (index may have changed)
            activity_index = None
            for i, act in enumerate(activities):
                if act.get("id") == activity_id:
                    activity_index = i
                    break
            if activity_index is None:
                logger.error(f"[schedule] Activity {activity_id} lost during overlap handling")
                return False
        
        # Update fields
        if activity_index is not None:
            if "start_time" in params:
                activities[activity_index]["start_time"] = params["start_time"]
            if "end_time" in params:
                activities[activity_index]["end_time"] = params["end_time"]
            if "activity_name" in params:
                activities[activity_index]["activity_name"] = params["activity_name"]
            if "responsiveness" in params:
                activities[activity_index]["responsiveness"] = int(params["responsiveness"])
            if "description" in params:
                activities[activity_index]["description"] = params["description"]
        
        schedule["activities"] = activities
        
        # Sort activities by start_time
        schedule["activities"] = _sort_activities(schedule["activities"])
        
        # Save schedule
        agent._save_schedule(schedule)
        
        logger.info(f"[{agent.name}] Updated schedule entry: {activity_id}")
        return True
        
    except Exception as e:
        logger.error(f"[schedule] Error updating schedule entry: {e}")
        return False


async def _handle_delete_schedule(agent, task: TaskNode) -> bool:
    """Delete a schedule entry."""
    try:
        params = task.params or {}
        activity_id = params.get("id")
        
        if not activity_id:
            logger.warning("[schedule] Missing id for delete")
            return False
        
        # Load existing schedule
        schedule = agent._load_schedule()
        if not schedule:
            logger.warning(f"[{agent.name}] No schedule found for delete")
            return False
        
        # Remove the activity
        activities = schedule.get("activities", [])
        original_count = len(activities)
        schedule["activities"] = [act for act in activities if act.get("id") != activity_id]
        
        if len(schedule["activities"]) == original_count:
            logger.warning(f"[{agent.name}] Schedule entry {activity_id} not found for delete")
            return False
        
        # Sort activities by start_time (order shouldn't change, but ensures consistency)
        schedule["activities"] = _sort_activities(schedule["activities"])
        
        # Save schedule
        agent._save_schedule(schedule)
        
        logger.info(f"[{agent.name}] Deleted schedule entry: {activity_id}")
        return True
        
    except Exception as e:
        logger.error(f"[schedule] Error deleting schedule entry: {e}")
        return False

