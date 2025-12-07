# admin_console/agents/conversation_llm.py
#
# Conversation-specific LLM management routes for the admin console.

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_default_llm
from config import STATE_DIRECTORY
from memory_storage import load_property_entries, write_property_entries

logger = logging.getLogger(__name__)


def register_conversation_llm_routes(agents_bp: Blueprint):
    """Register conversation-specific LLM routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation-llm/<user_id>", methods=["GET"])
    def api_get_conversation_llm(agent_config_name: str, user_id: str):
        """Get conversation-specific LLM for a user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            conversation_llm = agent.get_channel_llm_model(channel_id)
            agent_default_llm = agent._llm_name or get_default_llm()
            available_llms = get_available_llms()

            # Mark which LLM is the agent's default
            for llm in available_llms:
                if llm["value"] == agent_default_llm:
                    llm["is_default"] = True
                else:
                    llm["is_default"] = False

            return jsonify({
                "conversation_llm": conversation_llm,
                "agent_default_llm": agent_default_llm,
                "available_llms": available_llms,
            })
        except Exception as e:
            logger.error(f"Error getting conversation LLM for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation-llm/<user_id>", methods=["PUT"])
    def api_update_conversation_llm(agent_config_name: str, user_id: str):
        """Update conversation-specific LLM for a user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            data = request.json
            llm_name = data.get("llm_name", "").strip()

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
            agent_default_llm = agent._llm_name or get_default_llm()

            # If setting to agent default, remove the conversation-specific LLM
            if llm_name == agent_default_llm or not llm_name:
                if memory_file.exists():
                    _, payload = load_property_entries(
                        memory_file, "plan", default_id_prefix="plan"
                    )
                    if payload and isinstance(payload, dict):
                        payload.pop("llm_model", None)
                        write_property_entries(
                            memory_file, "plan", payload.get("plan", []), payload=payload
                        )
            else:
                # Set conversation-specific LLM
                _, payload = load_property_entries(
                    memory_file, "plan", default_id_prefix="plan"
                )
                if payload is None:
                    payload = {}
                payload["llm_model"] = llm_name
                write_property_entries(
                    memory_file, "plan", payload.get("plan", []), payload=payload
                )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating conversation LLM for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
