# src/admin_console/agents/events.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Admin console API for events (scheduled actions).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import add_cache_busting_headers, get_agent_by_name

logger = logging.getLogger(__name__)


def _event_to_display(ev: dict, agent_tz: ZoneInfo) -> dict:
    """Convert event from DB shape to display shape (time in agent TZ as ISO string)."""
    out = {
        "id": ev["id"],
        "intent": ev.get("intent", ""),
        "interval": ev.get("interval"),
        "occurrences": ev.get("occurrences"),
    }
    if ev.get("time_utc"):
        dt_utc = datetime.fromisoformat(ev["time_utc"].replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=UTC)
        out["time"] = dt_utc.astimezone(agent_tz).isoformat()
    return out


def register_event_routes(agents_bp: Blueprint):
    """Register event management routes."""

    @agents_bp.route("/api/agents/<agent_config_name>/events/<user_id>", methods=["GET"])
    def api_get_events(agent_config_name: str, user_id: str):
        """Get events for a conversation from MySQL. Times in agent timezone."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from db import events as db_events
            raw = db_events.load_events(agent.agent_id, channel_id)
            tz = agent.timezone
            events = [_event_to_display(ev, tz) for ev in raw]
            # Timezone name and offset for the Events UI label (e.g. "America/Los_Angeles (UTC-08:00)")
            now_tz = datetime.now(tz)
            offset_sec = (now_tz.utcoffset() or timedelta(0)).total_seconds()
            sign = "+" if offset_sec >= 0 else "-"
            hours = int(abs(offset_sec) // 3600)
            mins = int((abs(offset_sec) % 3600) // 60)
            offset_str = f"UTC{sign}{hours:02d}:{mins:02d}"
            timezone_display = f"{agent.get_timezone_identifier()} ({offset_str})"
            response = jsonify({
                "events": events,
                "timezone_display": timezone_display,
            })
            return add_cache_busting_headers(response)
        except Exception as e:
            logger.error(f"Error getting events for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/events/<user_id>", methods=["POST"])
    def api_create_event(agent_config_name: str, user_id: str):
        """Create a new event. Body: intent, time (ISO in agent TZ), interval?, occurrences?."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.json or {}
            intent = (data.get("intent") or "").strip()
            time_str = data.get("time")
            if not intent or not time_str:
                return jsonify({"error": "intent and time are required"}), 400

            tz = agent.timezone
            try:
                dt_utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=tz)
                dt_utc = dt_utc.astimezone(UTC)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid time format"}), 400

            event_id = f"event-{uuid.uuid4().hex[:8]}"
            interval = data.get("interval")
            occurrences = data.get("occurrences")
            if occurrences is not None:
                try:
                    occurrences = int(occurrences)
                    if occurrences < 1:
                        occurrences = None
                except (TypeError, ValueError):
                    occurrences = None

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from db import events as db_events
            db_events.save_event(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                event_id=event_id,
                time_utc=dt_utc,
                intent=intent,
                interval_value=interval,
                occurrences=occurrences,
            )
            ev_display = _event_to_display(
                {"id": event_id, "time_utc": dt_utc.isoformat(), "intent": intent, "interval": interval, "occurrences": occurrences},
                tz,
            )
            return jsonify({"success": True, "event": ev_display})
        except Exception as e:
            logger.error(f"Error creating event for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/events/<user_id>/<event_id>", methods=["PUT"])
    def api_update_event(agent_config_name: str, user_id: str, event_id: str):
        """Update an event. Body can include intent, time, interval, occurrences."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from db import events as db_events
            raw_list = db_events.load_events(agent.agent_id, channel_id)
            existing = next((e for e in raw_list if e.get("id") == event_id), None)
            if not existing:
                return jsonify({"error": "Event not found"}), 404

            data = request.json or {}
            intent = data.get("intent")
            if intent is not None:
                intent = intent.strip()
            else:
                intent = existing.get("intent", "")
            time_str = data.get("time")
            if time_str is not None:
                tz = agent.timezone
                try:
                    dt_utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                    if dt_utc.tzinfo is None:
                        dt_utc = dt_utc.replace(tzinfo=tz)
                    dt_utc = dt_utc.astimezone(UTC)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid time format"}), 400
            else:
                dt_utc = datetime.fromisoformat(existing["time_utc"].replace("Z", "+00:00"))
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=UTC)

            interval = data.get("interval") if "interval" in data else existing.get("interval")
            occurrences = data.get("occurrences") if "occurrences" in data else existing.get("occurrences")
            if occurrences is not None:
                try:
                    occurrences = int(occurrences)
                    if occurrences < 1:
                        occurrences = None
                except (TypeError, ValueError):
                    occurrences = existing.get("occurrences")

            db_events.save_event(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                event_id=event_id,
                time_utc=dt_utc,
                intent=intent,
                interval_value=interval,
                occurrences=occurrences,
            )
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating event {event_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/events/<user_id>/<event_id>", methods=["DELETE"])
    def api_delete_event(agent_config_name: str, user_id: str, event_id: str):
        """Delete an event."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from db import events as db_events
            db_events.delete_event(agent.agent_id, channel_id, event_id)
            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting event {event_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
