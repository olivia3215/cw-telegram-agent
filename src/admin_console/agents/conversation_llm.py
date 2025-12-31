# admin_console/agents/conversation_llm.py
#
# Conversation-specific LLM management routes for the admin console.

import logging

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_available_llms, get_default_llm

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

            # Resolve user_id (which may be a username) to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Ensure channel_id is an integer
            try:
                channel_id = int(channel_id)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid channel ID"}), 400

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            # Get conversation LLM from MySQL directly (not through agent.get_channel_llm_model which might have caching issues)
            from db import conversation_llm as db_conversation_llm
            conversation_llm = db_conversation_llm.get_conversation_llm(agent.agent_id, channel_id)
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

            # Resolve user_id (which may be a username) to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Ensure channel_id is an integer
            try:
                channel_id = int(channel_id)
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid channel ID"}), 400

            data = request.json
            llm_name = data.get("llm_name", "").strip()

            # Update in MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            agent_default_llm = agent._llm_name or get_default_llm()
            
            logger.info(
                f"Updating conversation LLM for agent {agent_config_name} (agent_id={agent.agent_id}), "
                f"channel {channel_id}: llm_name='{llm_name}', agent_default='{agent_default_llm}'"
            )

            # Set or remove the conversation-specific LLM
            # set_conversation_llm will only store if different from default, and remove if matching default
            from db import conversation_llm
            conversation_llm.set_conversation_llm(agent.agent_id, channel_id, llm_name, agent_default_llm)
            
            if llm_name == agent_default_llm or not llm_name:
                logger.info(f"Removed conversation LLM override (using agent default)")
            else:
                logger.info(f"Set conversation LLM override to '{llm_name}'")

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating conversation LLM for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
