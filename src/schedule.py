# src/schedule.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Schedule management for agents with daily schedules.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from clock import clock

if TYPE_CHECKING:
    from agent import Agent

logger = logging.getLogger(__name__)


@dataclass
class ScheduleActivity:
    """Represents a single activity in an agent's schedule."""
    id: str
    start_time: datetime  # Timezone-aware
    end_time: datetime    # Timezone-aware
    activity_name: str    # Human-readable name
    responsiveness: int   # 0-100
    description: str      # Detailed description (includes foods, work details, location, etc.)

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleActivity":
        """Create ScheduleActivity from dictionary."""
        # Parse ISO format datetime strings
        start_time = datetime.fromisoformat(data["start_time"])
        end_time = datetime.fromisoformat(data["end_time"])
        
        # Validate that datetimes are timezone-aware
        # Comparing timezone-naive with timezone-aware datetimes raises TypeError
        if start_time.tzinfo is None:
            raise ValueError(
                f"start_time must be timezone-aware (got timezone-naive datetime: {data['start_time']}). "
                f"ISO 8601 datetime strings must include timezone offset (e.g., '2025-12-02T06:00:00-10:00')."
            )
        if end_time.tzinfo is None:
            raise ValueError(
                f"end_time must be timezone-aware (got timezone-naive datetime: {data['end_time']}). "
                f"ISO 8601 datetime strings must include timezone offset (e.g., '2025-12-02T06:00:00-10:00')."
            )
        
        return cls(
            id=data["id"],
            start_time=start_time,
            end_time=end_time,
            activity_name=data["activity_name"],
            responsiveness=data["responsiveness"],
            description=data["description"],
        )

    def to_dict(self) -> dict:
        """Convert ScheduleActivity to dictionary."""
        return {
            "id": self.id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "activity_name": self.activity_name,
            "responsiveness": self.responsiveness,
            "description": self.description,
        }


def get_current_activity(
    schedule: dict | None, now: datetime | None = None
) -> tuple[ScheduleActivity | None, timedelta | None, ScheduleActivity | None]:
    """
    Get the current activity, time remaining, and next activity.
    
    Args:
        schedule: Schedule dictionary with activities list, or None
        now: Current time (defaults to clock.now(UTC))
    
    Returns:
        Tuple of (current_activity, time_remaining, next_activity):
        - current_activity: The activity that is currently happening, or None
        - time_remaining: Time until current activity ends, or None if no current activity
        - next_activity: The next activity after the current one, or None if no next activity
    """
    if schedule is None:
        return (None, None, None)
    
    if now is None:
        now = clock.now(UTC)
    
    activities = schedule.get("activities", [])
    if not activities:
        return (None, None, None)
    
    # Parse activities into ScheduleActivity objects
    schedule_activities = []
    for act_data in activities:
        try:
            act = ScheduleActivity.from_dict(act_data)
            schedule_activities.append(act)
        except Exception as e:
            logger.warning(f"Failed to parse activity: {e}")
            continue
    
    # Sort by start_time
    schedule_activities.sort(key=lambda a: a.start_time)
    
    # Find current activity (now is between start_time and end_time)
    current_activity = None
    for act in schedule_activities:
        if act.start_time <= now <= act.end_time:
            current_activity = act
            break
    
    # Calculate time remaining
    time_remaining = None
    if current_activity:
        time_remaining = current_activity.end_time - now
        if time_remaining.total_seconds() < 0:
            time_remaining = None
    
    # Find next activity (start_time > now)
    next_activity = None
    for act in schedule_activities:
        if act.start_time > now:
            next_activity = act
            break
    
    return (current_activity, time_remaining, next_activity)


def get_responsiveness(
    schedule: dict | None, now: datetime | None = None
) -> int:
    """
    Get the agent's current responsiveness based on schedule.
    
    Args:
        schedule: Schedule dictionary with activities list, or None
        now: Current time (defaults to clock.now(UTC))
    
    Returns:
        Responsiveness value (0-100), or 100 if no schedule or no current activity
    """
    if schedule is None:
        return 100
    
    current_activity, _, _ = get_current_activity(schedule, now)
    if current_activity is None:
        return 100
    
    return current_activity.responsiveness


def get_agent_responsiveness(
    agent: "Agent | None", now: datetime | None = None
) -> int:
    """
    Get the current responsiveness for an agent from its schedule.

    Args:
        agent: Agent instance, or None
        now: Current time (defaults to clock.now(UTC))

    Returns:
        Responsiveness value (0-100). Returns 100 if agent is None, has no
        schedule, or schedule cannot be loaded.
    """
    if agent is None or not getattr(agent, "daily_schedule_description", None):
        return 100
    try:
        schedule = agent._load_schedule()
        return get_responsiveness(schedule, now)
    except Exception:
        return 100


def get_wake_time(
    schedule: dict | None, now: datetime | None = None
) -> datetime | None:
    """
    Get the time when the agent will wake up (next activity with responsiveness > 0).
    
    Args:
        schedule: Schedule dictionary with activities list, or None
        now: Current time (defaults to clock.now(UTC))
    
    Returns:
        Wake time datetime, or None if agent is not asleep or no schedule
    """
    if schedule is None:
        return None
    
    if now is None:
        now = clock.now(UTC)
    
    current_activity, _, _ = get_current_activity(schedule, now)
    
    # If not asleep (responsiveness > 0), no wake time needed
    if current_activity is None or current_activity.responsiveness > 0:
        return None
    
    # Find next activity with responsiveness > 0
    activities = schedule.get("activities", [])
    schedule_activities = []
    for act_data in activities:
        try:
            act = ScheduleActivity.from_dict(act_data)
            schedule_activities.append(act)
        except Exception:
            continue
    
    schedule_activities.sort(key=lambda a: a.start_time)
    
    for act in schedule_activities:
        if act.start_time > now and act.responsiveness > 0:
            return act.start_time
    
    return None


def days_remaining(schedule: dict | None, now: datetime | None = None) -> float:
    """
    Calculate how many days of schedule remain from now.
    
    Args:
        schedule: Schedule dictionary with activities list, or None
        now: Current time (defaults to clock.now(UTC))
    
    Returns:
        Number of days remaining (0.0 if no schedule or no activities)
    """
    if schedule is None:
        return 0.0
    
    if now is None:
        now = clock.now(UTC)
    
    activities = schedule.get("activities", [])
    if not activities:
        return 0.0
    
    # Find the latest end_time
    latest_end = None
    for act_data in activities:
        try:
            act = ScheduleActivity.from_dict(act_data)
            if latest_end is None or act.end_time > latest_end:
                latest_end = act.end_time
        except Exception:
            continue
    
    if latest_end is None:
        return 0.0
    
    # Calculate days remaining
    time_remaining = latest_end - now
    return max(0.0, time_remaining.total_seconds() / 86400.0)
