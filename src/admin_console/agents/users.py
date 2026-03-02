# src/admin_console/agents/users.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Users tab API: active users list, profile, conversations summary, accounting."""

import logging

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.telegram_id_to_name import get_map_snapshot, get_map_snapshot_full

logger = logging.getLogger(__name__)


def _format_user_display_label(channel_telegram_id: int, info: dict | None) -> str:
    """Format dropdown label as 'Name (telegramid) [@username]' or just id when no info."""
    if not info:
        return str(channel_telegram_id)
    name = (info.get("name") or "").strip()
    username = (info.get("username") or "").strip()
    if not name:
        return str(channel_telegram_id)
    label = f"{name} ({channel_telegram_id})"
    if username:
        label += f" [@{username}]"
    return label


def _parse_days_param(default_days: int = 7) -> int:
    """Parse days query parameter with basic bounds checking."""
    raw_days = request.args.get("days")
    if not raw_days:
        return default_days
    try:
        days = int(raw_days)
    except ValueError:
        return default_days
    return max(1, min(days, 30))


def register_users_routes(agents_bp: Blueprint):
    """Register API routes for the Users main tab."""

    @agents_bp.route("/api/users/active", methods=["GET"])
    def api_users_active():
        """Return users with at least one llm_usage in the past week, most recent first."""
        try:
            from db.task_log import get_users_with_llm_activity

            days = _parse_days_param(default_days=7)
            users = get_users_with_llm_activity(days=days)
            id_to_info = get_map_snapshot_full()
            id_to_name = get_map_snapshot()
            for u in users:
                cid = u["channel_telegram_id"]
                u["display_name"] = _format_user_display_label(cid, id_to_info.get(str(cid)))
            return jsonify({"users": users, "days": days, "telegram_id_to_name": id_to_name})
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/users/<user_id>/profile", methods=["GET"])
    def api_user_profile(user_id: str):
        """Return agent_config_name and user_id so the frontend can load partner-profile from that agent."""
        try:
            channel_id = int(user_id)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid user ID"}), 400
        try:
            from agent import get_agent_for_id
            from db.task_log import get_user_cost_logs

            days = _parse_days_param(default_days=7)
            result = get_user_cost_logs(channel_id, days=days)
            logs = result.get("logs") or []
            agent_ids = {log["agent_telegram_id"] for log in logs if log.get("agent_telegram_id") is not None}
            if not agent_ids:
                return jsonify({"error": "No conversation found for this user"}), 404
            agents_with_activity = []
            for aid in agent_ids:
                agent = get_agent_for_id(aid)
                if agent and agent.config_name:
                    agents_with_activity.append(agent)
            if not agents_with_activity:
                return jsonify({"error": "No conversation found for this user"}), 404
            agents_with_activity.sort(key=lambda a: (a.config_name or ""))
            chosen = agents_with_activity[0]
            return jsonify({
                "agent_config_name": chosen.config_name,
                "user_id": str(channel_id),
            })
        except Exception as e:
            logger.error(f"Error getting user profile redirect for {user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/users/<user_id>/conversations", methods=["GET"])
    def api_user_conversations(user_id: str):
        """Return list of agents with total cost for this user (past 7 days)."""
        try:
            channel_id = int(user_id)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid user ID"}), 400
        try:
            from agent import get_agent_for_id
            from db.task_log import get_user_conversations_summary

            days = _parse_days_param(default_days=7)
            summary = get_user_conversations_summary(channel_id, days=days)
            conversations = []
            for row in summary:
                agent = get_agent_for_id(row["agent_telegram_id"])
                if not agent or not agent.config_name:
                    continue
                conversations.append({
                    "agent_config_name": agent.config_name,
                    "agent_name": agent.name,
                    "agent_telegram_id": row["agent_telegram_id"],
                    "total_cost": row["total_cost"],
                })
            return jsonify({"conversations": conversations, "days": days})
        except Exception as e:
            logger.error(f"Error getting user conversations for {user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/users/<user_id>/accounting", methods=["GET"])
    def api_user_accounting(user_id: str):
        """Return chronological cost logs for this user (any agent), past 7 days."""
        try:
            channel_id = int(user_id)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid user ID"}), 400
        try:
            from db.task_log import get_user_cost_logs

            days = _parse_days_param(default_days=7)
            result = get_user_cost_logs(channel_id, days=days)
            return jsonify({
                "days": days,
                "total_cost": result["total_cost"],
                "logs": result["logs"],
            })
        except Exception as e:
            logger.error(f"Error getting user accounting for {user_id}: {e}")
            return jsonify({"error": str(e)}), 500
