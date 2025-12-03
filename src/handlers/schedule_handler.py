# handlers/schedule_handler.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Handler for schedule tasks that allow agents to manage their daily schedules.
"""

import json
import logging
import uuid
from datetime import datetime

from handlers.registry import register_immediate_task_handler
from schedule import ScheduleActivity
from task_graph import TaskNode

logger = logging.getLogger(__name__)


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
        
        # Add new activity
        schedule["activities"].append(activity_dict)
        
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
        
        # Find the activity to update
        activities = schedule.get("activities", [])
        found = False
        
        for i, act in enumerate(activities):
            if act.get("id") == activity_id:
                # Update fields
                if "start_time" in params:
                    # Validate timezone-aware datetime
                    try:
                        start_time = datetime.fromisoformat(params["start_time"])
                        if start_time.tzinfo is None:
                            logger.warning(
                                f"[schedule] start_time must be timezone-aware (got: {params['start_time']}). "
                                f"ISO 8601 datetime strings must include timezone offset."
                            )
                            return False
                        activities[i]["start_time"] = params["start_time"]
                    except ValueError as e:
                        logger.warning(f"[schedule] Invalid start_time format: {e}")
                        return False
                if "end_time" in params:
                    # Validate timezone-aware datetime
                    try:
                        end_time = datetime.fromisoformat(params["end_time"])
                        if end_time.tzinfo is None:
                            logger.warning(
                                f"[schedule] end_time must be timezone-aware (got: {params['end_time']}). "
                                f"ISO 8601 datetime strings must include timezone offset."
                            )
                            return False
                        activities[i]["end_time"] = params["end_time"]
                    except ValueError as e:
                        logger.warning(f"[schedule] Invalid end_time format: {e}")
                        return False
                if "activity_name" in params:
                    activities[i]["activity_name"] = params["activity_name"]
                if "responsiveness" in params:
                    activities[i]["responsiveness"] = int(params["responsiveness"])
                if "description" in params:
                    activities[i]["description"] = params["description"]
                
                found = True
                break
        
        if not found:
            logger.warning(f"[{agent.name}] Schedule entry {activity_id} not found for update")
            return False
        
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
        
        # Save schedule
        agent._save_schedule(schedule)
        
        logger.info(f"[{agent.name}] Deleted schedule entry: {activity_id}")
        return True
        
    except Exception as e:
        logger.error(f"[schedule] Error deleting schedule entry: {e}")
        return False

