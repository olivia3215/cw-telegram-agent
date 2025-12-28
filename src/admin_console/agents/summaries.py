# admin_console/agents/summaries.py
#
# Summary management routes for the admin console.

import asyncio
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

            # Check if we should use MySQL or filesystem
            use_mysql = (
                STORAGE_BACKEND == "mysql"
                and hasattr(agent, "agent_id")
                and agent.agent_id is not None
            )
            
            if use_mysql:
                # Load from MySQL
                from db import summaries as db_summaries
                summaries = db_summaries.load_summaries(agent.agent_id, channel_id)
            else:
                # Load from filesystem
                summary_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
                summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")

            # Sort by message ID range (oldest first)
            summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))

            return jsonify({"summaries": summaries})
        except MemoryStorageError as e:
            logger.error(f"Error loading summaries for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
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

            # Check if we should use MySQL or filesystem
            use_mysql = (
                STORAGE_BACKEND == "mysql"
                and hasattr(agent, "agent_id")
                and agent.agent_id is not None
            )
            
            if use_mysql:
                # Update in MySQL
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
                
                # Extract metadata (fields that are not core fields)
                # load_summaries() merges metadata directly into the summary dict, so we need to extract
                # all fields that are not core fields
                core_fields = {"id", "content", "min_message_id", "max_message_id", "first_message_date", "last_message_date", "created"}
                metadata = {k: v for k, v in existing_summary.items() if k not in core_fields}
                
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
                    metadata=metadata,
                )
            else:
                # Update in filesystem
                summary_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

                def update_summary(entries, payload):
                    for entry in entries:
                        if entry.get("id") == summary_id:
                            if content is not None:
                                entry["content"] = content
                            if min_message_id is not None:
                                entry["min_message_id"] = min_message_id
                            if max_message_id is not None:
                                entry["max_message_id"] = max_message_id
                            if first_message_date is not None:
                                # Only update if not empty (empty strings should preserve existing value)
                                stripped_date = first_message_date.strip() if first_message_date else ""
                                if stripped_date:
                                    entry["first_message_date"] = stripped_date
                            if last_message_date is not None:
                                # Only update if not empty (empty strings should preserve existing value)
                                stripped_date = last_message_date.strip() if last_message_date else ""
                                if stripped_date:
                                    entry["last_message_date"] = stripped_date
                            break
                    return entries, payload

                mutate_property_entries(
                    summary_file, "summary", default_id_prefix="summary", mutator=update_summary
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

            # Check if we should use MySQL or filesystem
            use_mysql = (
                STORAGE_BACKEND == "mysql"
                and hasattr(agent, "agent_id")
                and agent.agent_id is not None
            )
            
            if use_mysql:
                # Delete from MySQL
                from db import summaries as db_summaries
                db_summaries.delete_summary(agent.agent_id, channel_id, summary_id)
            else:
                # Delete from filesystem
                summary_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

                def delete_summary(entries, payload):
                    entries = [e for e in entries if e.get("id") != summary_id]
                    return entries, payload

                mutate_property_entries(
                    summary_file, "summary", default_id_prefix="summary", mutator=delete_summary
                )

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

            # Check if we should use MySQL or filesystem
            use_mysql = (
                STORAGE_BACKEND == "mysql"
                and hasattr(agent, "agent_id")
                and agent.agent_id is not None
            )
            
            if use_mysql:
                # Create in MySQL
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
                    metadata={"origin": "puppetmaster"},
                )
            else:
                # Create in filesystem
                summary_file = Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"

                def create_summary(entries, payload):
                    entries.append(new_entry)
                    return entries, payload

                mutate_property_entries(
                    summary_file, "summary", default_id_prefix="summary", mutator=create_summary
                )

            return jsonify({"success": True, "summary": new_entry})
        except Exception as e:
            logger.error(f"Error creating summary for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
