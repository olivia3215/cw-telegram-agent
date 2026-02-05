# schedule_extension.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Schedule extension logic for agents with daily schedules.
"""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from clock import clock
from handlers.received_helpers.task_parsing import parse_llm_reply_from_json
from handlers.registry import dispatch_immediate_task
from schedule import ScheduleActivity, days_remaining

if TYPE_CHECKING:
    from agent import Agent

logger = logging.getLogger(__name__)


async def extend_schedule(agent: "Agent", start_date: datetime | None = None) -> dict:
    """
    Extend agent's schedule using LLM with task-based approach.
    
    Args:
        agent: Agent instance with schedule enabled
        start_date: Date to start extension from (defaults to now)
    
    Returns:
        Updated schedule dictionary
    """
    if not agent.daily_schedule_description:
        raise ValueError(f"Agent {agent.name} does not have a daily schedule configured")
    
    # Load existing schedule
    existing_schedule = agent._load_schedule()
    
    # Determine start_date: use provided value, or end of existing schedule, or now
    if start_date is None:
        if existing_schedule and existing_schedule.get("activities"):
            # Find the latest end_time in the schedule
            latest_end = None
            for act_data in existing_schedule.get("activities", []):
                try:
                    act = ScheduleActivity.from_dict(act_data)
                    if latest_end is None or act.end_time > latest_end:
                        latest_end = act.end_time
                except Exception:
                    continue
            
            if latest_end:
                # Start from the end of the existing schedule
                start_date = latest_end
            else:
                # No valid activities, start from now
                start_date = clock.now(agent.timezone)
        else:
            # No schedule, start from now
            start_date = clock.now(agent.timezone)
    
    # Build system prompt using Instructions-Schedule.md
    system_prompt = _build_schedule_system_prompt(agent, existing_schedule, start_date)
    
    # Get LLM instance
    llm = agent.llm
    
    # Extract allowed task types from Instructions-Schedule.md
    from prompt_loader import load_system_prompt
    from llm.task_schema import extract_task_types_from_prompt
    schedule_prompt = load_system_prompt("Instructions-Schedule")
    allowed_task_types = extract_task_types_from_prompt(schedule_prompt)
    
    # Query LLM using normal structured query (not JSON schema)
    now_iso = clock.now(agent.timezone).isoformat()
    
    try:
        model_name = getattr(llm, "model_name", None) or type(llm).__name__
        logger.info(
            "[%s] Schedule extension using model: %s",
            agent.name,
            model_name,
        )
        reply = await llm.query_structured(
            system_prompt=system_prompt,
            now_iso=now_iso,
            chat_type="direct",  # Schedule extension is not channel-specific
            history=[],  # No conversation history for schedule extension
            history_size=llm.history_size,
            timeout_s=None,
            allowed_task_types=allowed_task_types,
        )
    except Exception as e:
        logger.error(f"[{agent.name}] LLM query failed during schedule extension: {e}")
        raise
    
    if not reply:
        logger.warning(f"[{agent.name}] LLM returned empty response for schedule extension")
        return existing_schedule or {
            "version": "1.0",
            "agent_name": agent.name,
            "timezone": agent.get_timezone_identifier(),
            "last_extended": None,
            "activities": [],
        }
    
    # Parse tasks from response
    try:
        tasks = await parse_llm_reply_from_json(
            reply,
            agent_id=None,  # Not channel-specific
            channel_id=0,  # Not channel-specific
            agent=agent,
        )
    except Exception as e:
        logger.error(f"[{agent.name}] Failed to parse LLM response for schedule extension: {e}")
        logger.error(f"Response: {reply[:500]}")
        raise
    
    # Execute schedule tasks (and think tasks)
    schedule_count = 0
    for task in tasks:
        handled = await dispatch_immediate_task(task, agent=agent, channel_id=0)
        if task.type == "schedule":
            if handled:
                schedule_count += 1
        elif task.type == "think":
            # Think tasks are executed above via dispatch_immediate_task
            # They don't need any special handling here
            pass
        else:
            # Log unexpected task types (should only be schedule/think for schedule extension)
            logger.warning(
                f"[{agent.name}] Unexpected task type '{task.type}' in schedule extension response, ignoring"
            )
    
    # Reload schedule to get updated version
    updated_schedule = agent._load_schedule()
    
    logger.info(
        f"[{agent.name}] Processed {schedule_count} schedule task(s), "
        f"now has {days_remaining(updated_schedule):.1f} days remaining"
    )
    
    return updated_schedule


def _build_schedule_system_prompt(
    agent: "Agent", existing_schedule: dict | None, start_date: datetime
) -> str:
    """
    Build the system prompt for schedule extension using Instructions-Schedule.md.
    
    This uses the agent's prompt system but with Instructions-Schedule.md instead of Instructions.md.
    """
    from prompt_loader import load_system_prompt
    from core.prompt_utils import substitute_templates
    
    # Get recent activities for context
    recent_activities_text = ""
    if existing_schedule:
        activities = existing_schedule.get("activities", [])
        # Get last 3 days of activities
        cutoff_date = start_date - timedelta(days=3)
        recent_activities = []
        for act_data in activities:
            try:
                act = ScheduleActivity.from_dict(act_data)
                if act.end_time >= cutoff_date:
                    recent_activities.append(act)
            except Exception:
                continue
        
        if recent_activities:
            recent_activities.sort(key=lambda a: a.start_time)
            recent_activities_text = "Your recent schedule (last few days):\n"
            for act in recent_activities[-10:]:  # Last 10 activities
                recent_activities_text += f"- {act.activity_name} ({act.start_time.strftime('%Y-%m-%d %H:%M')} - {act.end_time.strftime('%H:%M')}): {act.description}\n"
    
    # Calculate end date: midnight of the day after next
    # This ensures we extend to the end of the next day, giving us at least a full day of coverage
    # Example: if start_date is 10 PM on Dec 2, end_date will be midnight of Dec 4 (26 hours)
    start_date_midnight = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date_midnight + timedelta(days=2)
    
    # Load Instructions-Schedule.md
    instructions_prompt = load_system_prompt("Instructions-Schedule")
    
    # Build specific instructions with time range
    specific_instructions = (
        "# Instruction\n\n"
        f"You are extending your daily schedule. Please create schedule entries starting from {start_date.strftime('%Y-%m-%d %H:%M:%S %Z')}.\n"
        f"The schedule should extend until {end_date.strftime('%Y-%m-%d %H:%M:%S %Z')} (midnight of the day after next).\n"
        f"Make sure activities don't overlap. Activities should cover from {start_date.strftime('%Y-%m-%d %H:%M:%S %Z')} until at least {end_date.strftime('%Y-%m-%d %H:%M:%S %Z')}.\n"
        "If the last activity is sleep, it should continue past the end time until the normal wake time (e.g., 06:00:00 the next day).\n"
    )
    
    # Add dynamic sections after the base instructions
    dynamic_sections = []
    
    # Add typical schedule and preferences
    if agent.daily_schedule_description:
        dynamic_sections.append(f"## Your Typical Schedule and Preferences\n\n{agent.daily_schedule_description}")
    
    # Add recent activities context
    if recent_activities_text:
        # Remove the leading newlines from recent_activities_text since we're adding a header
        recent_text = recent_activities_text.strip()
        if recent_text:
            dynamic_sections.append(f"## Recent Schedule Context\n\n{recent_text}")
    
    # Add agent instructions
    agent_instructions = ""
    if agent.instructions:
        agent_instructions = f"# Agent Instructions\n\n{agent.instructions}"
    
    # Combine all parts
    prompt_parts = [
        specific_instructions,
        instructions_prompt,
    ]
    
    # Add dynamic sections after the base instructions
    if dynamic_sections:
        prompt_parts.extend(dynamic_sections)
    
    if agent_instructions:
        prompt_parts.append(agent_instructions)
    
    # Join and apply template substitution
    final_prompt = "\n\n".join(prompt_parts)
    final_prompt = substitute_templates(final_prompt, agent.name, "Schedule Extension")
    
    return final_prompt
