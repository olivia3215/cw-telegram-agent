# admin_console/agents/memory.py
#
# Memory management routes for the admin console.

import json
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


def register_memory_routes(agents_bp: Blueprint):
    """Register memory management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/memories", methods=["GET"])
    def api_get_memories(agent_config_name: str):
        """Get memories for an agent (from state/AgentName/memory.json)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"
            memories, _ = load_property_entries(
                memory_file, "memory", default_id_prefix="memory"
            )

            # Sort by created timestamp (newest first)
            memories.sort(key=lambda x: x.get("created", ""), reverse=True)

            return jsonify({"memories": memories})
        except MemoryStorageError as e:
            logger.error(f"Error loading memories for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
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

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"

            def update_memory(entries, payload):
                for entry in entries:
                    if entry.get("id") == memory_id:
                        entry["content"] = content
                        break
                return entries, payload

            mutate_property_entries(
                memory_file, "memory", default_id_prefix="memory", mutator=update_memory
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

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"

            def delete_memory(entries, payload):
                entries = [e for e in entries if e.get("id") != memory_id]
                return entries, payload

            mutate_property_entries(
                memory_file, "memory", default_id_prefix="memory", mutator=delete_memory
            )

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

            memory_file = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"
            
            memory_id = f"memory-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": memory_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }

            def create_memory(entries, payload):
                entries.append(new_entry)
                return entries, payload

            mutate_property_entries(
                memory_file, "memory", default_id_prefix="memory", mutator=create_memory
            )

            return jsonify({"success": True, "memory": new_entry})
        except Exception as e:
            logger.error(f"Error creating memory for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/curated-memories", methods=["GET"])
    def api_get_curated_memories(agent_config_name: str):
        """Get curated memories for an agent (from configdir/agents/AgentName/memory/UserID.json)."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            if not agent.config_directory:
                return jsonify({"curated_memories": []})

            memory_dir = (
                Path(agent.config_directory) / "agents" / agent.config_name / "memory"
            )
            if not memory_dir.exists():
                return jsonify({"curated_memories": []})

            curated_memories = []
            for memory_file in memory_dir.glob("*.json"):
                user_id = memory_file.stem
                try:
                    with open(memory_file, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            memories = loaded.get("memory", [])
                        elif isinstance(loaded, list):
                            memories = loaded
                        else:
                            continue

                        # Sort by created timestamp (newest first)
                        memories.sort(
                            key=lambda x: x.get("created", ""), reverse=True
                        )

                        curated_memories.append(
                            {
                                "user_id": user_id,
                                "memories": memories,
                            }
                        )
                except Exception as e:
                    logger.warning(f"Error loading curated memory file {memory_file}: {e}")
                    continue

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

            if not agent.config_directory:
                return jsonify({"memories": []})

            memory_file = (
                Path(agent.config_directory)
                / "agents"
                / agent.config_name
                / "memory"
                / f"{user_id}.json"
            )

            if not memory_file.exists():
                return jsonify({"memories": []})

            try:
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                    elif isinstance(loaded, list):
                        memories = loaded
                    else:
                        memories = []

                    # Sort by created timestamp (newest first)
                    memories.sort(key=lambda x: x.get("created", ""), reverse=True)

                    return jsonify({"memories": memories})
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing curated memory file {memory_file}: {e}")
                return jsonify({"error": f"Corrupted JSON file: {e}"}), 500
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

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json
            content = data.get("content", "").strip()

            memory_file = (
                Path(agent.config_directory)
                / "agents"
                / agent.config_name
                / "memory"
                / f"{user_id}.json"
            )

            # Load existing data
            if memory_file.exists():
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                        payload = {k: v for k, v in loaded.items() if k != "memory"}
                    elif isinstance(loaded, list):
                        memories = loaded
                        payload = None
                    else:
                        memories = []
                        payload = None
            else:
                memories = []
                payload = None

            # Update the memory entry
            for entry in memories:
                if entry.get("id") == memory_id:
                    entry["content"] = content
                    break

            # Save back
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            if payload is not None:
                payload["memory"] = memories
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            else:
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(memories, f, indent=2, ensure_ascii=False)

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

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            memory_file = (
                Path(agent.config_directory)
                / "agents"
                / agent.config_name
                / "memory"
                / f"{user_id}.json"
            )

            if not memory_file.exists():
                return jsonify({"error": "Memory file not found"}), 404

            # Load existing data
            with open(memory_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    memories = loaded.get("memory", [])
                    payload = {k: v for k, v in loaded.items() if k != "memory"}
                elif isinstance(loaded, list):
                    memories = loaded
                    payload = None
                else:
                    return jsonify({"error": "Invalid file format"}), 500

            # Remove the memory entry
            memories = [e for e in memories if e.get("id") != memory_id]

            # Save back
            if payload is not None:
                payload["memory"] = memories
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            else:
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(memories, f, indent=2, ensure_ascii=False)

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

            if not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400

            data = request.json or {}
            content = data.get("content", "").strip()
            
            if not content:
                return jsonify({"error": "Content is required"}), 400

            memory_file = (
                Path(agent.config_directory)
                / "agents"
                / agent.config_name
                / "memory"
                / f"{user_id}.json"
            )

            # Load existing data
            if memory_file.exists():
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                        payload = {k: v for k, v in loaded.items() if k != "memory"}
                    elif isinstance(loaded, list):
                        memories = loaded
                        payload = None
                    else:
                        memories = []
                        payload = None
            else:
                memories = []
                payload = None

            memory_id = f"memory-{uuid.uuid4().hex[:8]}"
            created_value = normalize_created_string(None, agent)
            
            new_entry = {
                "id": memory_id,
                "content": content,
                "created": created_value,
                "origin": "puppetmaster"
            }
            
            memories.append(new_entry)

            # Save back
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            if payload is not None:
                payload["memory"] = memories
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
            else:
                with open(memory_file, "w", encoding="utf-8") as f:
                    json.dump(memories, f, indent=2, ensure_ascii=False)

            return jsonify({"success": True, "memory": new_entry})
        except Exception as e:
            logger.error(
                f"Error creating curated memory for {agent_config_name}/{user_id}: {e}"
            )
            return jsonify({"error": str(e)}), 500

