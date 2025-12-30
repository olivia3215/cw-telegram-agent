# admin_console/agents/memory.py
#
# Memory management routes for the admin console.

import json
import logging
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_default_llm
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
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503
            
            from db.memories import load_memories
            memories = load_memories(agent.agent_id)

            # Sort by created timestamp (newest first)
            memories.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"memories": memories})
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
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
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
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
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
            if not hasattr(agent, "agent_id") or agent.agent_id is None:
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

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories", methods=["GET"])
    def api_get_curated_memories(agent_config_name: str):
        """Get curated memories for an agent (from MySQL)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"curated_memories": []})

            from db import curated_memories as db_curated_memories
            
            # Get all channels that have curated memories for this agent
            # We need to query distinct channel_ids
            from db.connection import get_db_connection
            with get_db_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        SELECT DISTINCT channel_id
                        FROM curated_memories
                        WHERE agent_telegram_id = %s
                        """,
                        (agent.agent_id,),
                    )
                    channel_rows = cursor.fetchall()
                    channel_ids = [row["channel_id"] for row in channel_rows]
                finally:
                    cursor.close()

            curated_memories = []
            for channel_id in channel_ids:
                memories = db_curated_memories.load_curated_memories(agent.agent_id, channel_id)
                # Sort by created timestamp (newest first)
                memories.sort(key=lambda x: x.get("created", ""), reverse=True)
                curated_memories.append(
                    {
                        "user_id": str(channel_id),
                        "memories": memories,
                    }
                )

            return jsonify({"curated_memories": curated_memories})
        except Exception as e:
            logger.error(f"Error getting curated memories for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories/<user_id>", methods=["GET"])
    def api_get_curated_memories_for_user(agent_config_name: str, user_id: str):
        """Get curated memories for a specific user."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"memories": []})

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            from db import curated_memories as db_curated_memories
            memories = db_curated_memories.load_curated_memories(agent.agent_id, channel_id)
            # Sort by created timestamp (newest first)
            memories.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"memories": memories})
        except Exception as e:
            logger.error(
                f"Error getting curated memories for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories/<user_id>/<memory_id>", methods=["PUT"])
    def api_update_curated_memory(agent_config_name: str, user_id: str, memory_id: str):
        """Update a curated memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.json
            content = data.get("content", "").strip()

            # Load existing memory to preserve created timestamp and metadata
            from db import curated_memories as db_curated_memories
            memories = db_curated_memories.load_curated_memories(agent.agent_id, channel_id)
            
            # Find the memory entry
            memory_entry = None
            for entry in memories:
                if entry.get("id") == memory_id:
                    memory_entry = entry
                    break
            
            if not memory_entry:
                return jsonify({"error": "Memory not found"}), 404

            # Update content, preserve created and other metadata
            created = memory_entry.get("created")
            # Extract metadata (everything except id, content, created)
            metadata = {k: v for k, v in memory_entry.items() if k not in {"id", "content", "created"}}
            
            # Save updated memory
            db_curated_memories.save_curated_memory(
                agent.agent_id,
                channel_id,
                memory_id,
                content,
                created=created,
            )

            return jsonify({"success": True})
        except Exception as e:
            logger.error(
                f"Error updating curated memory {memory_id} for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories/<user_id>/<memory_id>", methods=["DELETE"])
    def api_delete_curated_memory(agent_config_name: str, user_id: str, memory_id: str):
        """Delete a curated memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            from db import curated_memories as db_curated_memories
            db_curated_memories.delete_curated_memory(agent.agent_id, channel_id, memory_id)

            return jsonify({"success": True})
        except Exception as e:
            logger.error(
                f"Error deleting curated memory {memory_id} for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories/<user_id>", methods=["POST"])
    def api_create_curated_memory(agent_config_name: str, user_id: str):
        """Create a new curated memory entry."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not hasattr(agent, "agent_id") or agent.agent_id is None:
                return jsonify({"error": "Agent not authenticated"}), 503

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            memory_id = f"memory-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": memory_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }
            
            from db import curated_memories as db_curated_memories
            db_curated_memories.save_curated_memory(
                agent.agent_id,
                channel_id,
                memory_id,
                content,
                created=created_value,
            )

            return jsonify({"success": True, "memory": new_entry})
        except Exception as e:
            logger.error(
                f"Error creating curated memory for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/partner-content-check", methods=["POST"])
    def api_check_partner_content_batch(agent_config_name: str):
        """
        Batch check which partners have content for curated-memories, conversation-llm, and plans.
        
        Request body: {"user_ids": ["user_id1", "user_id2", ...]}
        Response: {
            "content_checks": {
                "user_id1": {
                    "curated_memories": true,
                    "conversation_llm": false,
                    "plans": true
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
                    "curated_memories": False,
                    "conversation_llm": False,
                    "plans": False
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
            
            # Check curated memories and plans for each channel
            for user_id_str, channel_id in user_id_to_channel_id.items():
                checks = content_checks[user_id_str]
                
                # Check curated memories
                try:
                    from db import curated_memories as db_curated_memories
                    memories = db_curated_memories.load_curated_memories(agent.agent_id, channel_id)
                    checks["curated_memories"] = len(memories) > 0
                except Exception:
                    checks["curated_memories"] = False
                
                # Check plans
                try:
                    from db.plans import load_plans
                    plans = load_plans(agent.agent_id, channel_id)
                    checks["plans"] = len(plans) > 0
                except Exception:
                    checks["plans"] = False
                
            return jsonify({"content_checks": content_checks})
        except Exception as e:
            logger.error(f"Error checking partner content for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
