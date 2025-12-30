# admin_console/agents/intentions.py
#
# Intention management routes for the admin console.

import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from utils.time import normalize_created_string

logger = logging.getLogger(__name__)


def register_intention_routes(agents_bp: Blueprint):
    """Register intention management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/intentions", methods=["GET"])
    def api_get_intentions(agent_config_name: str):
        """Get intentions for an agent from MySQL."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Load from MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.intentions import load_intentions
            intentions = load_intentions(agent.agent_id)

            # Sort by created timestamp (newest first)
            intentions.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"intentions": intentions})
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

            # Update in MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.intentions import load_intentions, save_intention
            # Load existing intention to preserve other fields
            intentions = load_intentions(agent.agent_id)
            intention = next((i for i in intentions if i.get("id") == intention_id), None)
            if not intention:
                return jsonify({"error": "Intention not found"}), 404
            # Update content and save
            save_intention(
                agent_telegram_id=agent.agent_id,
                intention_id=intention_id,
                content=content,
                created=intention.get("created"),
                metadata={k: v for k, v in intention.items() if k not in {"id", "content", "created"}},
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

            # Delete from MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.intentions import delete_intention
            delete_intention(agent.agent_id, intention_id)

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

            intention_id = f"intent-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": intention_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            # Save to MySQL
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.intentions import save_intention
            save_intention(
                agent_telegram_id=agent.agent_id,
                intention_id=intention_id,
                content=content,
                created=created_value,
                metadata={"origin": "puppetmaster"},
            )

            return jsonify({"success": True, "intention": new_entry})
        except Exception as e:
            logger.error(f"Error creating intention for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
