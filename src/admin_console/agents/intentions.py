# admin_console/agents/intentions.py
#
# Intention management routes for the admin console.

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


def register_intention_routes(agents_bp: Blueprint):
    """Register intention management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/intentions", methods=["GET"])
    def api_get_intentions(agent_config_name: str):
        """Get intentions for an agent (from state/AgentName/memory.json)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"
            intentions, _ = load_property_entries(
                memory_file, "intention", default_id_prefix="intent"
            )

            # Sort by created timestamp (newest first)
            intentions.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"intentions": intentions})
        except MemoryStorageError as e:
            logger.error(f"Error loading intentions for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting intentions for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/intentions/<intention_id>", methods=["PUT"])
    def api_update_intention(agent_config_name: str, intention_id: str):
        """Update an intention entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json
            content = data.get("content", "").strip()

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"

            def update_intention(entries, payload):
                for entry in entries:
                    if entry.get("id") == intention_id:
                        entry["content"] = content
                        break
                return entries, payload

            mutate_property_entries(
                memory_file, "intention", default_id_prefix="intent", mutator=update_intention
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating intention {intention_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/intentions/<intention_id>", methods=["DELETE"])
    def api_delete_intention(agent_config_name: str, intention_id: str):
        """Delete an intention entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"

            def delete_intention(entries, payload):
                entries = [e for e in entries if e.get("id") != intention_id]
                return entries, payload

            mutate_property_entries(
                memory_file, "intention", default_id_prefix="intent", mutator=delete_intention
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting intention {intention_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/intentions", methods=["POST"])
    def api_create_intention(agent_config_name: str):
        """Create a new intention entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"
            
            intention_id = f"intent-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": intention_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            def create_intention(entries, payload):
                entries.append(new_entry)
                return entries, payload

            mutate_property_entries(
                memory_file, "intention", default_id_prefix="intent", mutator=create_intention
            )

            return jsonify({"success": True, "intention": new_entry})
        except Exception as e:
            logger.error(f"Error creating intention for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
