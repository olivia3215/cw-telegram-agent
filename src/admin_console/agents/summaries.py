# admin_console/agents/summaries.py
#
# Summary management routes for the admin console.

import asyncio
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


def register_summary_routes(agents_bp: Blueprint):
    """Register summary management routes."""
    
    @agents_bp.route("/api/agents/<agent_name>/summaries/<user_id>", methods=["GET"])
    def api_get_summaries(agent_name: str, user_id: str):
        """Get summaries for a conversation."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            # Trigger backfill for missing dates using agent's executor (runs in agent's thread)
            try:
                async def _backfill_dates():
                    try:
                        storage = agent._storage
                        if storage:
                            await storage.backfill_summary_dates(channel_id, agent)
                    except Exception as e:
                        logger.warning(f"Backfill failed for {agent_name}/{user_id}: {e}", exc_info=True)
                
                # Schedule backfill in agent's thread (non-blocking, fire-and-forget)
                executor = agent.executor
                if executor and executor.loop and executor.loop.is_running():
                    # Schedule the coroutine without waiting for it
                    asyncio.run_coroutine_threadsafe(_backfill_dates(), executor.loop)
                    logger.info(f"Scheduled backfill for {agent_name}/{user_id} (channel {channel_id})")
                else:
                    logger.info(
                        f"Agent executor not available for {agent_name}, skipping backfill. "
                        f"executor={executor}, loop={executor.loop if executor else None}, "
                        f"is_running={executor.loop.is_running() if executor and executor.loop else None}"
                    )
            except Exception as e:
                # Don't fail the request if backfill setup fails
                logger.warning(f"Failed to setup backfill for {agent_name}/{user_id}: {e}", exc_info=True)

            summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
            summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")

            # Sort by message ID range (oldest first)
            summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))

            return jsonify({"summaries": summaries})
        except MemoryStorageError as e:
            logger.error(f"Error loading summaries for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting summaries for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/summaries/<user_id>/<summary_id>", methods=["PUT"])
    def api_update_summary(agent_name: str, user_id: str, summary_id: str):
        """Update a summary entry."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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

            summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"

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
            logger.error(f"Error updating summary {summary_id} for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/summaries/<user_id>/<summary_id>", methods=["DELETE"])
    def api_delete_summary(agent_name: str, user_id: str, summary_id: str):
        """Delete a summary entry."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

            try:
                channel_id = int(user_id)
            except ValueError:
                return jsonify({"error": "Invalid user ID"}), 400

            summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"

            def delete_summary(entries, payload):
                entries = [e for e in entries if e.get("id") != summary_id]
                return entries, payload

            mutate_property_entries(
                summary_file, "summary", default_id_prefix="summary", mutator=delete_summary
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting summary {summary_id} for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_name>/summaries/<user_id>", methods=["POST"])
    def api_create_summary(agent_name: str, user_id: str):
        """Create a new summary entry."""
        try:
            agent = get_agent_by_name(agent_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

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

            summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
            
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

            def create_summary(entries, payload):
                entries.append(new_entry)
                return entries, payload

            mutate_property_entries(
                summary_file, "summary", default_id_prefix="summary", mutator=create_summary
            )

            return jsonify({"success": True, "summary": new_entry})
        except Exception as e:
            logger.error(f"Error creating summary for {agent_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

