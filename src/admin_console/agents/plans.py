# admin_console/agents/plans.py
#
# Plan management routes for the admin console.

import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from utils.time import normalize_created_string

logger = logging.getLogger(__name__)


def register_plan_routes(agents_bp: Blueprint):
    """Register plan management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/plans/<user_id>", methods=["GET"])
    def api_get_plans(agent_config_name: str, user_id: str):
        """Get plans for a conversation from MySQL."""
        try:
            logger.info(f"Loading plans for agent {agent_config_name}, user_id {user_id}")
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                logger.warning(f"Agent {agent_config_name} not found")
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                logger.warning(f"Error resolving user_id {user_id} for agent {agent_config_name}: {error_response[0].get_json()}")
                return error_response
            logger.info(f"Resolved user_id {user_id} to channel_id {channel_id} for agent {agent_config_name}")

            # Load from MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                logger.warning(f"Agent {agent_config_name} has no Telegram ID, cannot load plans from MySQL")
                return jsonify({"error": "Agent not authenticated. Please ensure the agent is logged in."}), 503
            
            try:
                from db.plans import load_plans
                plans = load_plans(agent.agent_id, channel_id)
                logger.debug(f"Loaded {len(plans)} plans from MySQL for agent {agent_config_name}, channel {channel_id}")
            except Exception as e:
                logger.error(f"Error loading plans from MySQL for {agent_config_name}/{channel_id}: {e}")
                return jsonify({"error": f"Error loading plans from MySQL: {str(e)}"}), 500

            # Sort by created timestamp (newest first)
            # Handle case where plans might be None or empty
            if not plans:
                plans = []
            else:
                plans.sort(key=lambda x: x.get("created", "") or "", reverse=True)

            logger.debug(f"Returning {len(plans)} plans for {agent_config_name}/{user_id} (channel_id: {channel_id})")
            return jsonify({"plans": plans})
        except Exception as e:
            logger.error(f"Error getting plans for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/plans/<user_id>/<plan_id>", methods=["PUT"])
    def api_update_plan(agent_config_name: str, user_id: str, plan_id: str):
        """Update a plan entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.json
            content = data.get("content", "").strip()

            # Update in MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.plans import load_plans, save_plan
            # Load existing plan to preserve other fields
            plans = load_plans(agent.agent_id, channel_id)
            plan = next((p for p in plans if p.get("id") == plan_id), None)
            if not plan:
                return jsonify({"error": "Plan not found"}), 404
            # Update content and save
            save_plan(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                plan_id=plan_id,
                content=content,
                created=plan.get("created"),
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating plan {plan_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/plans/<user_id>/<plan_id>", methods=["DELETE"])
    def api_delete_plan(agent_config_name: str, user_id: str, plan_id: str):
        """Delete a plan entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Delete from MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.plans import delete_plan
            delete_plan(agent.agent_id, channel_id, plan_id)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting plan {plan_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/plans/<user_id>", methods=["POST"])
    def api_create_plan(agent_config_name: str, user_id: str):
        """Create a new plan entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": plan_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            # Save to MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.plans import save_plan
            save_plan(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                plan_id=plan_id,
                content=content,
                created=created_value,
            )

            return jsonify({"success": True, "plan": new_entry})
        except Exception as e:
            logger.error(f"Error creating plan for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
