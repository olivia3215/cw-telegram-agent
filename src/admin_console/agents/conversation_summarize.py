# admin_console/agents/conversation_summarize.py
#
# Summarization route for conversations.

import logging

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, resolve_user_id_to_channel_id
from handlers.received import parse_llm_reply
from handlers.received_helpers.summarization import trigger_summarization_directly

logger = logging.getLogger(__name__)


def register_conversation_summarize_routes(agents_bp: Blueprint):
    """Register conversation summarization route."""

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/summarize", methods=["POST"])
    def api_trigger_summarization(agent_config_name: str, user_id: str):
        """Trigger summarization for a conversation directly without going through the task graph."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"success": False, "error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"success": False, "error": "Agent not authenticated"}), 400

            if not agent.client or not agent.client.is_connected():
                return jsonify({"success": False, "error": "Agent client not connected"}), 503

            # Trigger summarization directly (without going through task graph)
            # This is async, so we need to run it on the agent's event loop
            async def _trigger_summarize():
                channel_id = await resolve_user_id_to_channel_id(agent, user_id)
                await trigger_summarization_directly(agent, channel_id, parse_llm_reply_fn=parse_llm_reply)

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                agent.execute(_trigger_summarize(), timeout=60.0)  # Increased timeout for summarization
                return jsonify({"success": True, "message": "Summarization completed successfully"})
            except ValueError as e:
                return jsonify({"success": False, "error": str(e)}), 400
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"success": False, "error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error triggering summarization: {e}")
                    return jsonify({"success": False, "error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout triggering summarization for agent {agent_config_name}, user {user_id}")
                return jsonify({"success": False, "error": "Timeout triggering summarization"}), 504
        except Exception as e:
            logger.error(f"Error triggering summarization for {agent_config_name}/{user_id}: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
