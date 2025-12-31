# admin_console/agents/summaries.py
#
# Summary management routes for the admin console.

import asyncio
import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from utils.time import normalize_created_string

logger = logging.getLogger(__name__)


def register_summary_routes(agents_bp: Blueprint):
    """Register summary management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/summaries/<user_id>", methods=["GET"])
    def api_get_summaries(agent_config_name: str, user_id: str):
        """Get summaries for a conversation."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Load from MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db import summaries as db_summaries
            summaries = db_summaries.load_summaries(agent.agent_id, channel_id)

            # Sort by message ID range (oldest first)
            summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))

            return jsonify({"summaries": summaries})
        except Exception as e:
            logger.error(f"Error getting summaries for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/summaries/<user_id>/<summary_id>", methods=["PUT"])
    def api_update_summary(agent_config_name: str, user_id: str, summary_id: str):
        """Update a summary entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            data = request.json or {}
            content = data.get("content")
            if content is not None:
                content = content.strip()
            min_message_id = data.get("min_message_id")
            max_message_id = data.get("max_message_id")
            first_message_date = data.get("first_message_date")
            last_message_date = data.get("last_message_date")

            # Update in MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db import summaries as db_summaries
            # Load existing summary to get full metadata
            summaries_list = db_summaries.load_summaries(agent.agent_id, channel_id)
            existing_summary = None
            for s in summaries_list:
                if s.get("id") == summary_id:
                    existing_summary = s
                    break
            
            if not existing_summary:
                return jsonify({"error": "Summary not found"}), 404
            
            # Handle date fields - strip whitespace if provided
            updated_first_message_date = existing_summary.get("first_message_date")
            if first_message_date is not None:
                stripped_date = first_message_date.strip() if first_message_date else ""
                if stripped_date:
                    updated_first_message_date = stripped_date
            
            updated_last_message_date = existing_summary.get("last_message_date")
            if last_message_date is not None:
                stripped_date = last_message_date.strip() if last_message_date else ""
                if stripped_date:
                    updated_last_message_date = stripped_date
            
            # Save updated summary
            db_summaries.save_summary(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                summary_id=summary_id,
                content=content if content is not None else existing_summary.get("content", ""),
                min_message_id=min_message_id if min_message_id is not None else existing_summary.get("min_message_id"),
                max_message_id=max_message_id if max_message_id is not None else existing_summary.get("max_message_id"),
                first_message_date=updated_first_message_date,
                last_message_date=updated_last_message_date,
                created=existing_summary.get("created"),
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating summary {summary_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/summaries/<user_id>/<summary_id>", methods=["DELETE"])
    def api_delete_summary(agent_config_name: str, user_id: str, summary_id: str):
        """Delete a summary entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Delete from MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db import summaries as db_summaries
            db_summaries.delete_summary(agent.agent_id, channel_id, summary_id)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting summary {summary_id} for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/summaries/<user_id>", methods=["POST"])
    def api_create_summary(agent_config_name: str, user_id: str):
        """Create a new summary entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            data = request.json or {}
            content = data.get("content", "").strip()
            min_message_id = data.get("min_message_id")
            max_message_id = data.get("max_message_id")
            first_message_date = data.get("first_message_date")
            last_message_date = data.get("last_message_date")
            
            if not content:
                return jsonify({"error": "Content is required"}), 400
            if min_message_id is None or max_message_id is None:
                return jsonify({"error": "min_message_id and max_message_id are required"}), 400

            summary_id = f"summary-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": summary_id,
                "content": content,
                "min_message_id": min_message_id,
                "max_message_id": max_message_id,
                "created": created_value,
                "origin": "puppetmaster"
            }
            
            if first_message_date:
                new_entry["first_message_date"] = first_message_date.strip()
            if last_message_date:
                new_entry["last_message_date"] = last_message_date.strip()

            # Create in MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db import summaries as db_summaries
            db_summaries.save_summary(
                agent_telegram_id=agent.agent_id,
                channel_id=channel_id,
                summary_id=summary_id,
                content=content,
                min_message_id=min_message_id,
                max_message_id=max_message_id,
                first_message_date=new_entry.get("first_message_date"),
                last_message_date=new_entry.get("last_message_date"),
                created=created_value,
            )

            return jsonify({"success": True, "summary": new_entry})
        except Exception as e:
            logger.error(f"Error creating summary for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
