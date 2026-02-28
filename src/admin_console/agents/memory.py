# src/admin_console/agents/memory.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json
import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import add_cache_busting_headers, get_agent_by_name, get_default_llm
from utils.time import normalize_created_string

logger = logging.getLogger(__name__)


def register_memory_routes(agents_bp: Blueprint):
    """Register memory management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/memories", methods=["GET"])
    def api_get_memories(agent_config_name: str):
        """Get memories for an agent from MySQL."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Load from MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.memories import load_memories
            memories = load_memories(agent.agent_id)

            # Sort by created timestamp (newest first)
            memories.sort(key=lambda x: x.get("created", ""), reverse=True)

            response = jsonify({"memories": memories})
            return add_cache_busting_headers(response)
        except Exception as e:
            logger.error(f"Error getting memories for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memories/<memory_id>", methods=["PUT"])
    def api_update_memory(agent_config_name: str, memory_id: str):
        """Update a memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json
            content = data.get("content", "").strip()

            # Update in MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.memories import load_memories, save_memory
            # Load existing memory to preserve other fields
            memories = load_memories(agent.agent_id)
            memory = next((m for m in memories if m.get("id") == memory_id), None)
            if not memory:
                return jsonify({"error": "Memory not found"}), 404
            # Update content and save
            save_memory(
                agent_telegram_id=agent.agent_id,
                memory_id=memory_id,
                content=content,
                created=memory.get("created"),
                creation_channel=memory.get("creation_channel"),
                creation_channel_id=memory.get("creation_channel_id"),
                creation_channel_username=memory.get("creation_channel_username"),
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error updating memory {memory_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memories/<memory_id>", methods=["DELETE"])
    def api_delete_memory(agent_config_name: str, memory_id: str):
        """Delete a memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Delete from MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.memories import delete_memory
            delete_memory(agent.agent_id, memory_id)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(f"Error deleting memory {memory_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/memories", methods=["POST"])
    def api_create_memory(agent_config_name: str):
        """Create a new memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            memory_id = f"memory-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": memory_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            # Save to MySQL
            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.memories import save_memory
            save_memory(
                agent_telegram_id=agent.agent_id,
                memory_id=memory_id,
                content=content,
                created=created_value,
            )

            return jsonify({"success": True, "memory": new_entry})
        except Exception as e:
            logger.error(f"Error creating memory for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/notes", methods=["GET"])
    def api_get_notes(agent_config_name: str):
        """Get notes for an agent (from MySQL)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"notes": []})

            from db import notes as db_notes
            
            # Get all channels that have notes for this agent
            # We need to query distinct channel_ids
            from db.connection import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        SELECT DISTINCT channel_id
                        FROM notes
                        WHERE agent_telegram_id = %s
                        """,
                        (agent.agent_id,),
                    )
                    channel_rows = cursor.fetchall()
                    channel_ids = [row["channel_id"] for row in channel_rows]
                finally:
                    cursor.close()

            notes_list = []
            for channel_id in channel_ids:
                channel_notes = db_notes.load_notes(agent.agent_id, channel_id)
                # Sort by created timestamp (newest first)
                channel_notes.sort(key=lambda x: x.get("created", ""), reverse=True)
                notes_list.append(
                    {
                        "user_id": str(channel_id),
                        "notes": channel_notes,
                    }
                )

            response = jsonify({"notes": notes_list})
            return add_cache_busting_headers(response)
        except Exception as e:
            logger.error(f"Error getting notes for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/notes/<user_id>", methods=["GET"])
    def api_get_notes_for_user(agent_config_name: str, user_id: str):
        """Get notes for a specific user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"notes": []})

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            from db import notes as db_notes
            notes_list = db_notes.load_notes(agent.agent_id, channel_id)
            # Sort by created timestamp (newest first)
            notes_list.sort(key=lambda x: x.get("created", ""), reverse=True)

            response = jsonify({"notes": notes_list})
            return add_cache_busting_headers(response)
        except Exception as e:
            logger.error(
                f"Error getting notes for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/notes/<user_id>/<note_id>", methods=["PUT"])
    def api_update_note(agent_config_name: str, user_id: str, note_id: str):
        """Update a note entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.json or {}
            content = data.get("content", "").strip()

            # Load existing note to preserve created timestamp and metadata
            from db import notes as db_notes
            notes_list = db_notes.load_notes(agent.agent_id, channel_id)
            
            # Find the note entry
            note_entry = None
            for entry in notes_list:
                if entry.get("id") == note_id:
                    note_entry = entry
                    break
            
            if not note_entry:
                return jsonify({"error": "Note not found"}), 404

            # Update content, preserve created and other metadata
            created = note_entry.get("created")
            # Extract metadata (everything except id, content, created)
            metadata = {k: v for k, v in note_entry.items() if k not in {"id", "content", "created"}}
            
            # Save updated note
            db_notes.save_note(
                agent.agent_id,
                channel_id,
                note_id,
                content,
                created=created,
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(
                f"Error updating note {note_id} for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/notes/<user_id>/<note_id>", methods=["DELETE"])
    def api_delete_note(agent_config_name: str, user_id: str, note_id: str):
        """Delete a note entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            from db import notes as db_notes
            db_notes.delete_note(agent.agent_id, channel_id, note_id)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(
                f"Error deleting note {note_id} for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/notes/<user_id>", methods=["POST"])
    def api_create_note(agent_config_name: str, user_id: str):
        """Create a new note entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.is_authenticated:
                return jsonify({"error": "Agent not authenticated"}), 503

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            note_id = f"note-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": note_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }
            
            from db import notes as db_notes
            db_notes.save_note(
                agent.agent_id,
                channel_id,
                note_id,
                content,
                created=created_value,
            )

            return jsonify({"success": True, "note": new_entry})
        except Exception as e:
            logger.error(
                f"Error creating note for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/partner-content-check", methods=["POST"])
    def api_check_partner_content_batch(agent_config_name: str):
        """
        Batch check which partners have content for notes, conversation overrides, and plans.
        
        Request body: {"user_ids": ["user_id1", "user_id2", ...]}
        Response: {
            "content_checks": {
                "user_id1": {
                    "notes": true,
                    "conversation_llm": false,
                    "conversation_gagged": true,
                    "conversation_parameters": true,
                    "plans": true,
                    "work_queue": true
                },
                ...
            }
        }
        """
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            data = request.json or {}
            user_ids = data.get("user_ids", [])
            
            if not isinstance(user_ids, list):
                return jsonify({"error": "user_ids must be a list"}), 400

            content_checks = {}
            
            # Convert user_ids to channel_ids and separate valid/invalid ones
            valid_channel_ids = []
            invalid_user_ids = []
            user_id_to_channel_id = {}
            
            for user_id_str in user_ids:
                try:
                    channel_id = int(user_id_str)
                    valid_channel_ids.append(channel_id)
                    user_id_to_channel_id[user_id_str] = channel_id
                except (ValueError, TypeError):
                    invalid_user_ids.append(user_id_str)
            
            # Initialize all checks to False
            for user_id_str in user_ids:
                content_checks[user_id_str] = {
                    "notes": False,
                    "conversation_llm": False,
                    "conversation_gagged": False,
                    "conversation_parameters": False,
                    "plans": False,
                    "events": False,
                    "work_queue": False
                }
            
            if not agent.agent_id:
                return jsonify({"content_checks": content_checks})
            
            # Bulk check conversation LLM overrides (presence in table = override)
            if valid_channel_ids:
                try:
                    from db import conversation_llm as db_conversation_llm
                    channels_with_overrides = db_conversation_llm.channels_with_conversation_llm_overrides(
                        agent.agent_id, valid_channel_ids
                    )
                    # Update content_checks for channels with overrides
                    for user_id_str, channel_id in user_id_to_channel_id.items():
                        if channel_id in channels_with_overrides:
                            content_checks[user_id_str]["conversation_llm"] = True
                except Exception as e:
                    logger.warning(f"Error bulk checking conversation LLM overrides: {e}")

            # Bulk check conversation gagged overrides (presence in table = override)
            if valid_channel_ids:
                try:
                    from db import conversation_gagged as db_conversation_gagged
                    channels_with_gagged_overrides = db_conversation_gagged.channels_with_conversation_gagged_overrides(
                        agent.agent_id, valid_channel_ids
                    )
                    for user_id_str, channel_id in user_id_to_channel_id.items():
                        if channel_id in channels_with_gagged_overrides:
                            content_checks[user_id_str]["conversation_gagged"] = True
                except Exception as e:
                    logger.warning(f"Error bulk checking conversation gagged overrides: {e}")
            
            # Check notes and plans for each channel
            for user_id_str, channel_id in user_id_to_channel_id.items():
                checks = content_checks[user_id_str]

                # Conversation-parameters marker: any per-conversation overrides we can detect cheaply.
                # (Muted is a Telegram-side setting and intentionally excluded here to avoid N API calls.)
                checks["conversation_parameters"] = bool(
                    checks.get("conversation_llm") or checks.get("conversation_gagged")
                )
                
                # Check notes
                try:
                    from db import notes as db_notes
                    notes_list = db_notes.load_notes(agent.agent_id, channel_id)
                    checks["notes"] = len(notes_list) > 0
                except Exception:
                    checks["notes"] = False
                
                # Check plans
                try:
                    from db.plans import load_plans
                    plans = load_plans(agent.agent_id, channel_id)
                    checks["plans"] = len(plans) > 0
                except Exception:
                    checks["plans"] = False

                # Check events
                try:
                    from db import events as db_events
                    events_list = db_events.load_events(agent.agent_id, channel_id)
                    checks["events"] = len(events_list) > 0
                except Exception:
                    checks["events"] = False
                
                # Check work queue
                try:
                    from task_graph import WorkQueue
                    work_queue = WorkQueue.get_instance()
                    graph = work_queue.graph_for_conversation(agent.agent_id, channel_id)
                    checks["work_queue"] = graph is not None and len(graph.tasks) > 0
                except Exception:
                    checks["work_queue"] = False
                
            return jsonify({"content_checks": content_checks})
        except Exception as e:
            logger.error(f"Error checking partner content for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
