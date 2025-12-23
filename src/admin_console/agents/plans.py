# admin_console/agents/plans.py
#
# Plan management routes for the admin console.

import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY
from memory_storage import (
    MemoryStorageError,
    load_property_entries,
    mutate_property_entries,
)
from utils.time import normalize_created_string

logger = logging.getLogger(__name__)


def register_plan_routes(agents_bp: Blueprint):
    """Register plan management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/plans/<user_id>", methods=["GET"])
    def api_get_plans(agent_config_name: str, user_id: str):
        """Get plans for a conversation."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
            plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")

            # Sort by created timestamp (newest first)
            plans.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"plans": plans})
        except MemoryStorageError as e:
            logger.error(f"Error loading plans for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
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

            plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

            def update_plan(entries, payload):
                for entry in entries:
                    if entry.get("id") == plan_id:
                        entry["content"] = content
                        break
                return entries, payload

            mutate_property_entries(
                plan_file, "plan", default_id_prefix="plan", mutator=update_plan
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

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

            def delete_plan(entries, payload):
                entries = [e for e in entries if e.get("id") != plan_id]
                return entries, payload

            mutate_property_entries(
                plan_file, "plan", default_id_prefix="plan", mutator=delete_plan
            )

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

            plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
            
            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": plan_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            def create_plan(entries, payload):
                entries.append(new_entry)
                return entries, payload

            mutate_property_entries(
                plan_file, "plan", default_id_prefix="plan", mutator=create_plan
            )

            return jsonify({"success": True, "plan": new_entry})
        except Exception as e:
            logger.error(f"Error creating plan for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
