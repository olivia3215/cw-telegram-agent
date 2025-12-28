# admin_console/agents/plans.py
#
# Plan management routes for the admin console.

import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY, STORAGE_BACKEND
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
        """Get plans for a conversation (from MySQL if enabled, otherwise from filesystem)."""
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

            if STORAGE_BACKEND == "mysql":
                # Load from MySQL
                from db.plans import load_plans
                if not agent.agent_id:
                    logger.warning(f"Agent {agent_config_name} has no Telegram ID, cannot load plans from MySQL")
                    return jsonify({"error": "Agent has no Telegram ID. Please ensure the agent is logged in."}), 400
                try:
                    plans = load_plans(agent.agent_id, channel_id)
                    logger.debug(f"Loaded {len(plans)} plans from MySQL for agent {agent_config_name}, channel {channel_id}")
                except Exception as e:
                    logger.error(f"Error loading plans from MySQL for {agent_config_name}/{channel_id}: {e}")
                    return jsonify({"error": f"Error loading plans from MySQL: {str(e)}"}), 500
            else:
                # Load from filesystem
                plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
                try:
                    plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")
                    logger.debug(f"Loaded {len(plans)} plans from filesystem for agent {agent_config_name}, channel {channel_id}")
                except Exception as e:
                    logger.warning(f"Error loading plans from filesystem for {agent_config_name}/{channel_id}: {e}")
                    plans = []

            # Sort by created timestamp (newest first)
            # Handle case where plans might be None or empty
            if not plans:
                plans = []
            else:
                plans.sort(key=lambda x: x.get("created", "") or "", reverse=True)

            logger.debug(f"Returning {len(plans)} plans for {agent_config_name}/{user_id} (channel_id: {channel_id})")
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

            if STORAGE_BACKEND == "mysql":
                # Update in MySQL
                from db.plans import load_plans, save_plan
                if not agent.agent_id:
                    return jsonify({"error": "Agent has no Telegram ID"}), 400
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
                    metadata={k: v for k, v in plan.items() if k not in {"id", "content", "created"}},
                )
            else:
                # Update in filesystem
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

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            if STORAGE_BACKEND == "mysql":
                # Delete from MySQL
                from db.plans import delete_plan
                if not agent.agent_id:
                    return jsonify({"error": "Agent has no Telegram ID"}), 400
                delete_plan(agent.agent_id, channel_id, plan_id)
            else:
                # Delete from filesystem
                plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

                def delete_plan_func(entries, payload):
                    entries = [e for e in entries if e.get("id") != plan_id]
                    return entries, payload

                mutate_property_entries(
                    plan_file, "plan", default_id_prefix="plan", mutator=delete_plan_func
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

            plan_id = f"plan-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": plan_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            if STORAGE_BACKEND == "mysql":
                # Save to MySQL
                from db.plans import save_plan
                if not agent.agent_id:
                    return jsonify({"error": "Agent has no Telegram ID"}), 400
                save_plan(
                    agent_telegram_id=agent.agent_id,
                    channel_id=channel_id,
                    plan_id=plan_id,
                    content=content,
                    created=created_value,
                    metadata={"origin": "puppetmaster"},
                )
            else:
                # Save to filesystem
                plan_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

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
