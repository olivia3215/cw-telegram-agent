# src/admin_console/agents/costs.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, resolve_user_id_and_handle_errors

logger = logging.getLogger(__name__)


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


def register_cost_routes(agents_bp: Blueprint):
    """Register cost log routes for conversation, agent, and global scopes."""

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/costs", methods=["GET"])
    def api_conversation_costs(agent_config_name: str, user_id: str):
        """Return weekly LLM usage cost logs for one conversation."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            from db.task_log import get_conversation_cost_logs

            days = _parse_days_param(default_days=7)
            result = get_conversation_cost_logs(agent.agent_id, channel_id, days=days)
            return jsonify(
                {
                    "days": days,
                    "total_cost": result["total_cost"],
                    "logs": result["logs"],
                }
            )
        except Exception as e:
            logger.error(f"Error loading conversation costs for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/costs", methods=["GET"])
    def api_agent_costs(agent_config_name: str):
        """Return weekly LLM usage cost logs for one agent across conversations."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from db.task_log import get_agent_cost_logs

            days = _parse_days_param(default_days=7)
            result = get_agent_cost_logs(agent.agent_id, days=days)
            return jsonify(
                {
                    "days": days,
                    "total_cost": result["total_cost"],
                    "logs": result["logs"],
                }
            )
        except Exception as e:
            logger.error(f"Error loading agent costs for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/global/costs", methods=["GET"])
    def api_global_costs():
        """Return weekly LLM usage cost logs globally."""
        try:
            from db.task_log import get_global_cost_logs

            days = _parse_days_param(default_days=7)
            result = get_global_cost_logs(days=days)
            return jsonify(
                {
                    "days": days,
                    "total_cost": result["total_cost"],
                    "logs": result["logs"],
                }
            )
        except Exception as e:
            logger.error(f"Error loading global costs: {e}")
            return jsonify({"error": str(e)}), 500
