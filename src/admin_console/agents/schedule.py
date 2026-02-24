# src/admin_console/agents/schedule.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Admin API for agent schedule (calendar) maintenance.
"""

import asyncio
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from schedule import ScheduleActivity

logger = logging.getLogger(__name__)


def _sort_activities(activities: list[dict]) -> list[dict]:
    """Sort activities by start_time."""
    def sort_key(act: dict):
        try:
            start_str = act.get("start_time", "")
            if not start_str:
                return (1, datetime.min)
            dt = datetime.fromisoformat(start_str)
            return (0, dt)
        except (ValueError, KeyError, TypeError):
            return (1, datetime.min)
    return sorted(activities, key=sort_key)


def _validate_and_normalize_activities(activities: list) -> tuple[list[dict] | None, str | None]:
    """
    Validate activities and return sorted list of dicts with timezone-aware datetimes.
    Returns (normalized_activities, None) on success, or (None, error_message) on failure.
    """
    if not isinstance(activities, list):
        return None, "activities must be a list"
    result = []
    for i, act in enumerate(activities):
        if not isinstance(act, dict):
            return None, f"activity at index {i} must be an object"
        try:
            obj = ScheduleActivity.from_dict(act)
            result.append(obj.to_dict())
        except Exception as e:
            return None, f"activity at index {i}: {e}"
    sorted_result = _sort_activities(result)
    # Check no overlaps (sorted: each end <= next start)
    for j in range(len(sorted_result) - 1):
        end_str = sorted_result[j].get("end_time", "")
        next_start_str = sorted_result[j + 1].get("start_time", "")
        if end_str and next_start_str:
            end_dt = datetime.fromisoformat(end_str)
            next_start = datetime.fromisoformat(next_start_str)
            if end_dt.tzinfo is None:
                return None, f"activity at index {j}: end_time must be timezone-aware"
            if next_start.tzinfo is None:
                return None, f"activity at index {j + 1}: start_time must be timezone-aware"
            if end_dt > next_start:
                return None, "activities must not overlap"
    return sorted_result, None


def register_schedule_routes(agents_bp: Blueprint):
    """Register schedule GET/PUT routes."""

    @agents_bp.route("/api/agents/<agent_config_name>/schedule", methods=["GET"])
    def api_get_schedule(agent_config_name: str):
        """Get agent's schedule (timezone, last_extended, activities sorted by start_time)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            schedule = agent._load_schedule()
            if schedule is None:
                schedule = {"activities": []}
            activities = schedule.get("activities", [])
            if activities:
                schedule = {**schedule, "activities": _sort_activities(activities)}
            # Always use the agent's current timezone from Parameters for display.
            # The schedule blob may contain a stale timezone (e.g. from server default
            # before the agent had a timezone set); the UI should show the agent's
            # configured timezone so it matches the Parameters tab.
            if hasattr(agent, "get_timezone_identifier"):
                schedule["timezone"] = agent.get_timezone_identifier()
            return jsonify({"success": True, **schedule})
        except Exception as e:
            logger.error(f"Error getting schedule for {agent_config_name}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/schedule", methods=["PUT"])
    def api_put_schedule(agent_config_name: str):
        """Replace agent's schedule. Body: { timezone?, last_extended?, activities }."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            data = request.get_json(force=True, silent=True) or {}
            activities = data.get("activities", [])
            normalized, err = _validate_and_normalize_activities(activities)
            if err:
                return jsonify({"error": err}), 400
            # Preserve timezone and last_extended if provided
            existing = agent._load_schedule() or {}
            schedule = {
                "timezone": data.get("timezone", existing.get("timezone")),
                "last_extended": data.get("last_extended", existing.get("last_extended")),
                "activities": normalized,
            }
            agent._save_schedule(schedule)
            return jsonify({"success": True, "message": "Schedule saved"})
        except Exception as e:
            logger.error(f"Error saving schedule for {agent_config_name}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/schedule/extend", methods=["POST"])
    def api_extend_schedule(agent_config_name: str):
        """Run the agent's schedule extension (LLM) and return the updated schedule."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not getattr(agent, "daily_schedule_description", None):
                return jsonify({"error": "Agent does not have a daily schedule configured"}), 400
            main_loop = None
            try:
                from main_loop import get_main_loop
                main_loop = get_main_loop()
            except Exception as e:
                logger.warning(f"Could not get main loop for schedule extend: {e}")
            if not main_loop or not main_loop.is_running():
                return jsonify({"error": "Main event loop is not available; cannot run schedule extension"}), 503
            from schedule_extension import extend_schedule
            future = asyncio.run_coroutine_threadsafe(extend_schedule(agent), main_loop)
            try:
                updated_schedule = future.result(timeout=120)
            except TimeoutError:
                return jsonify({"error": "Schedule extension timed out (120s)"}), 504
            except Exception as e:
                logger.error(f"Schedule extension failed for {agent_config_name}: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500
            if not updated_schedule:
                updated_schedule = {"activities": []}
            activities = updated_schedule.get("activities", [])
            if activities:
                updated_schedule = {**updated_schedule, "activities": _sort_activities(activities)}
            # Use agent's current timezone for display (same as GET schedule).
            if hasattr(agent, "get_timezone_identifier"):
                updated_schedule["timezone"] = agent.get_timezone_identifier()
            return jsonify({"success": True, **updated_schedule})
        except Exception as e:
            logger.error(f"Error extending schedule for {agent_config_name}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
