# admin_console/agents/conversation_work_queue.py
#
# Work queue viewing and management routes for the admin console.

import logging

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, resolve_user_id_to_channel_id
from task_graph import WorkQueue

logger = logging.getLogger(__name__)


def register_conversation_work_queue_routes(agents_bp: Blueprint):
    """Register conversation work queue routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/work-queue/<user_id>", methods=["GET"])
    def api_get_work_queue(agent_config_name: str, user_id: str):
        """Get work queue data for a specific conversation."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"error": "Agent not authenticated"}), 400

            # Resolve user_id to channel_id (handles @username, phone numbers, and numeric IDs)
            async def _resolve_channel_id():
                return await resolve_user_id_to_channel_id(agent, user_id)
            
            try:
                channel_id = agent.execute(_resolve_channel_id(), timeout=10.0)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error resolving user ID: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout resolving user ID for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout resolving user ID"}), 504

            # Get work queue singleton
            work_queue = WorkQueue.get_instance()

            # Find the graph for this conversation
            graph = work_queue.graph_for_conversation(agent.agent_id, channel_id)

            if not graph:
                return jsonify({"work_queue": None})

            # Serialize the graph to a JSON-friendly format
            work_queue_data = {
                "id": graph.id,
                "context": graph.context,
                "nodes": [
                    {
                        "id": task.id,
                        "type": task.type,
                        "params": task.params,
                        "depends_on": task.depends_on,
                        "status": task.status.value,
                    }
                    for task in graph.tasks
                ],
            }

            return jsonify({"work_queue": work_queue_data})

        except Exception as e:
            logger.error(f"Error getting work queue for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/work-queue/<user_id>", methods=["DELETE"])
    def api_delete_work_queue(agent_config_name: str, user_id: str):
        """Delete (cancel) all pending tasks in the conversation's work queue."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"error": "Agent not authenticated"}), 400

            # Resolve user_id to channel_id (handles @username, phone numbers, and numeric IDs)
            async def _resolve_channel_id():
                return await resolve_user_id_to_channel_id(agent, user_id)
            
            try:
                channel_id = agent.execute(_resolve_channel_id(), timeout=10.0)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error resolving user ID: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout resolving user ID for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout resolving user ID"}), 504

            # Get work queue singleton
            work_queue = WorkQueue.get_instance()

            # Find the graph for this conversation
            graph = work_queue.graph_for_conversation(agent.agent_id, channel_id)

            if not graph:
                return jsonify({"error": "No work queue found for this conversation"}), 404

            # Remove the graph from the work queue
            work_queue.remove(graph)

            # Save the updated work queue to disk
            work_queue.save()

            logger.info(f"Deleted work queue for {agent_config_name}/{user_id} (graph_id={graph.id})")
            return jsonify({"success": True, "message": "Work queue cleared successfully"})

        except Exception as e:
            logger.error(f"Error deleting work queue for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500
