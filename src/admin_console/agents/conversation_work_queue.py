# src/admin_console/agents/conversation_work_queue.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, resolve_user_id_and_handle_errors
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
                return jsonify({"success": False, "error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"success": False, "error": "Agent not authenticated"}), 400

            # Resolve user_id to channel_id (handles @username, phone numbers, and numeric IDs)
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Get work queue singleton
            work_queue = WorkQueue.get_instance()

            # Find the graph for this conversation
            graph = work_queue.graph_for_conversation(agent.agent_id, channel_id)

            if not graph:
                return jsonify({"success": True, "work_queue": None})

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

            return jsonify({"success": True, "work_queue": work_queue_data})

        except Exception as e:
            logger.error(f"Error getting work queue for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/work-queue/<user_id>", methods=["DELETE"])
    def api_delete_work_queue(agent_config_name: str, user_id: str):
        """Delete (cancel) all pending tasks in the conversation's work queue."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"success": False, "error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.agent_id:
                return jsonify({"success": False, "error": "Agent not authenticated"}), 400

            # Resolve user_id to channel_id (handles @username, phone numbers, and numeric IDs)
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Get work queue singleton
            work_queue = WorkQueue.get_instance()

            # Find the graph for this conversation
            graph = work_queue.graph_for_conversation(agent.agent_id, channel_id)

            if not graph:
                return jsonify({"success": False, "error": "No work queue found for this conversation"}), 404

            # Remove the graph from the work queue
            work_queue.remove(graph)

            # Save the updated work queue to disk
            work_queue.save()

            logger.info(f"Deleted work queue for {agent_config_name}/{user_id} (graph_id={graph.id})")
            return jsonify({"success": True, "message": "Work queue cleared successfully"})

        except Exception as e:
            logger.error(f"Error deleting work queue for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500
