# admin_console/agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent management routes for the admin console.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

# Import asyncio only when needed to avoid event loop issues in Flask threads
try:
    import asyncio
except ImportError:
    asyncio = None

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon.tl.types import User  # pyright: ignore[reportMissingImports]

import copy
import json as json_lib

from agent import Agent
from clock import clock
from config import STATE_DIRECTORY
from llm.media_helper import get_media_llm
from memory_storage import (
    MemoryStorageError,
    load_property_entries,
    mutate_property_entries,
    write_property_entries,
)
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from telegram_util import get_channel_name
from id_utils import normalize_peer_id
from admin_console.helpers import (
    get_agent_by_name,
    get_default_llm,
    get_available_llms,
    get_work_queue,
)
from register_agents import register_all_agents, all_agents as get_all_agents

logger = logging.getLogger(__name__)

# Create agents blueprint
agents_bp = Blueprint("agents", __name__)

@agents_bp.route("/api/agents", methods=["GET"])
def api_agents():
    """Get list of all agents."""
    try:
        register_all_agents()
        agents = list(get_all_agents())
        agent_list = [
            {
                "name": agent.name,
                "phone": agent.phone,
                "agent_id": agent.agent_id if agent.agent_id else None
            }
            for agent in agents
        ]
        return jsonify({"agents": agent_list})
    except Exception as e:
        logger.error(f"Error getting agents list: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/memories", methods=["GET"])
def api_get_memories(agent_name: str):
    """Get memories for an agent (from state/AgentName/memory.json)."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"
        memories, _ = load_property_entries(
            memory_file, "memory", default_id_prefix="memory"
        )

        # Sort by created timestamp (newest first)
        memories.sort(
            key=lambda x: x.get("created", ""), reverse=True
        )

        return jsonify({"memories": memories})
    except MemoryStorageError as e:
        logger.error(f"Error loading memories for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error getting memories for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/memories/<memory_id>", methods=["PUT"])
def api_update_memory(agent_name: str, memory_id: str):
    """Update a memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        data = request.json
        content = data.get("content", "").strip()

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"

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
        logger.error(f"Error updating memory {memory_id} for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/memories/<memory_id>", methods=["DELETE"])
def api_delete_memory(agent_name: str, memory_id: str):
    """Delete a memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"

        def delete_memory(entries, payload):
            entries = [e for e in entries if e.get("id") != memory_id]
            return entries, payload

        mutate_property_entries(
            memory_file, "memory", default_id_prefix="memory", mutator=delete_memory
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting memory {memory_id} for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/memories", methods=["POST"])
def api_create_memory(agent_name: str):
    """Create a new memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        data = request.json or {}
        content = data.get("content", "").strip()
        
        if not content:
            return jsonify({"error": "Content is required"}), 400

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"
        
        import uuid
        from time_utils import normalize_created_string
        
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
        logger.error(f"Error creating memory for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/curated-memories", methods=["GET"])
def api_get_curated_memories(agent_name: str):
    """Get curated memories for an agent (from configdir/agents/AgentName/memory/UserID.json)."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"curated_memories": []})

        memory_dir = (
            Path(agent.config_directory) / "agents" / agent_name / "memory"
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
        logger.error(f"Error getting curated memories for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/curated-memories/<user_id>", methods=["GET"])
def api_get_curated_memories_for_user(agent_name: str, user_id: str):
    """Get curated memories for a specific user."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"memories": []})

        memory_file = (
            Path(agent.config_directory)
            / "agents"
            / agent_name
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
            f"Error getting curated memories for {agent_name}/{user_id}: {e}"
        )
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/curated-memories/<user_id>/<memory_id>", methods=["PUT"])
def api_update_curated_memory(agent_name: str, user_id: str, memory_id: str):
    """Update a curated memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"error": "Agent has no config directory"}), 400

        data = request.json
        content = data.get("content", "").strip()

        memory_file = (
            Path(agent.config_directory)
            / "agents"
            / agent_name
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
            f"Error updating curated memory {memory_id} for {agent_name}/{user_id}: {e}"
        )
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/curated-memories/<user_id>/<memory_id>", methods=["DELETE"])
def api_delete_curated_memory(agent_name: str, user_id: str, memory_id: str):
    """Delete a curated memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"error": "Agent has no config directory"}), 400

        memory_file = (
            Path(agent.config_directory)
            / "agents"
            / agent_name
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
            f"Error deleting curated memory {memory_id} for {agent_name}/{user_id}: {e}"
        )
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/curated-memories/<user_id>", methods=["POST"])
def api_create_curated_memory(agent_name: str, user_id: str):
    """Create a new curated memory entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"error": "Agent has no config directory"}), 400

        data = request.json or {}
        content = data.get("content", "").strip()
        
        if not content:
            return jsonify({"error": "Content is required"}), 400

        memory_file = (
            Path(agent.config_directory)
            / "agents"
            / agent_name
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

        import uuid
        from time_utils import normalize_created_string
        
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
            f"Error creating curated memory for {agent_name}/{user_id}: {e}"
        )
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/intentions", methods=["GET"])
def api_get_intentions(agent_name: str):
    """Get intentions for an agent (from state/AgentName/memory.json)."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"
        intentions, _ = load_property_entries(
            memory_file, "intention", default_id_prefix="intent"
        )

        # Sort by created timestamp (newest first)
        intentions.sort(key=lambda x: x.get("created", ""), reverse=True)

        return jsonify({"intentions": intentions})
    except MemoryStorageError as e:
        logger.error(f"Error loading intentions for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error getting intentions for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/intentions/<intention_id>", methods=["PUT"])
def api_update_intention(agent_name: str, intention_id: str):
    """Update an intention entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        data = request.json
        content = data.get("content", "").strip()

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"

        def update_intention(entries, payload):
            for entry in entries:
                if entry.get("id") == intention_id:
                    entry["content"] = content
                    break
            return entries, payload

        mutate_property_entries(
            memory_file, "intention", default_id_prefix="intent", mutator=update_intention
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating intention {intention_id} for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/intentions/<intention_id>", methods=["DELETE"])
def api_delete_intention(agent_name: str, intention_id: str):
    """Delete an intention entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory.json"

        def delete_intention(entries, payload):
            entries = [e for e in entries if e.get("id") != intention_id]
            return entries, payload

        mutate_property_entries(
            memory_file, "intention", default_id_prefix="intent", mutator=delete_intention
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting intention {intention_id} for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/configuration", methods=["GET"])
def api_get_agent_configuration(agent_name: str):
    """Get agent configuration (LLM and prompt)."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        # Get current LLM (agent's configured LLM or default)
        current_llm = agent._llm_name or get_default_llm()
        available_llms = get_available_llms()

        # Mark which LLM is the default
        default_llm = get_default_llm()
        for llm in available_llms:
            if llm["value"] == default_llm:
                llm["is_default"] = True
            else:
                llm["is_default"] = False

        return jsonify({
            "llm": current_llm,
            "available_llms": available_llms,
            "prompt": agent.instructions,
        })
    except Exception as e:
        logger.error(f"Error getting configuration for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/configuration/llm", methods=["PUT"])
def api_update_agent_llm(agent_name: str):
    """Update agent LLM configuration."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"error": "Agent has no config directory"}), 400

        data = request.json
        llm_name = data.get("llm_name", "").strip()

        # Find agent's markdown file
        agent_file = Path(agent.config_directory) / "agents" / f"{agent_name}.md"
        if not agent_file.exists():
            return jsonify({"error": "Agent configuration file not found"}), 404

        # Read and parse the markdown file
        content = agent_file.read_text(encoding="utf-8")
        from register_agents import extract_fields_from_markdown
        fields = extract_fields_from_markdown(content)

        # Update LLM field (remove if set to default)
        default_llm = get_default_llm()
        if llm_name == default_llm or not llm_name:
            # Remove LLM field to use default
            if "LLM" in fields:
                del fields["LLM"]
        else:
            fields["LLM"] = llm_name

        # Reconstruct markdown file
        lines = []
        for field_name, field_value in fields.items():
            lines.append(f"# {field_name}")
            lines.append(str(field_value).strip())
            lines.append("")

        agent_file.write_text("\n".join(lines), encoding="utf-8")

        # Reload agent
        from register_agents import parse_agent_markdown, register_telegram_agent
        parsed = parse_agent_markdown(agent_file)
        if parsed:
            # Disconnect old agent's client if connected
            # Use agent.execute() to schedule disconnect on the client's event loop
            if agent._client:
                try:
                    async def _disconnect_old_client():
                        try:
                            if agent._client and agent._client.is_connected():
                                await agent._client.disconnect()
                        except Exception as e:
                            logger.warning(f"Error disconnecting old client for {agent_name}: {e}")
                    
                    # Schedule disconnect on the client's event loop
                    agent.execute(_disconnect_old_client())
                except Exception as e:
                    logger.warning(f"Error scheduling disconnect for {agent_name}: {e}")
                
                # Clear the client reference to prevent using it in wrong event loop
                agent._client = None
                agent._loop = None  # Clear cached loop when client is cleared
            
            # Create new LLM instance
            from llm.factory import create_llm_from_name
            new_llm = create_llm_from_name(llm_name if llm_name else None)

            # Update agent in registry
            register_telegram_agent(
                name=parsed["name"],
                phone=parsed["phone"],
                instructions=parsed["instructions"],
                role_prompt_names=parsed["role_prompt_names"],
                sticker_set_names=parsed.get("sticker_set_names") or [],
                explicit_stickers=parsed.get("explicit_stickers") or [],
                config_directory=agent.config_directory,
                timezone=parsed.get("timezone"),
                llm_name=llm_name if llm_name else None,
                llm=new_llm,
            )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating LLM for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/configuration/prompt", methods=["PUT"])
def api_update_agent_prompt(agent_name: str):
    """Update agent prompt (instructions)."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.config_directory:
            return jsonify({"error": "Agent has no config directory"}), 400

        data = request.json
        prompt = data.get("prompt", "").strip()

        # Find agent's markdown file
        agent_file = Path(agent.config_directory) / "agents" / f"{agent_name}.md"
        if not agent_file.exists():
            return jsonify({"error": "Agent configuration file not found"}), 404

        # Read and parse the markdown file
        content = agent_file.read_text(encoding="utf-8")
        from register_agents import extract_fields_from_markdown
        fields = extract_fields_from_markdown(content)

        # Update Agent Instructions field
        fields["Agent Instructions"] = prompt

        # Reconstruct markdown file
        lines = []
        for field_name, field_value in fields.items():
            lines.append(f"# {field_name}")
            lines.append(str(field_value).strip())
            lines.append("")

        agent_file.write_text("\n".join(lines), encoding="utf-8")

        # Reload agent
        from register_agents import parse_agent_markdown, register_telegram_agent
        parsed = parse_agent_markdown(agent_file)
        if parsed:
            # Disconnect old agent's client if connected
            # Use agent.execute() to schedule disconnect on the client's event loop
            if agent._client:
                try:
                    async def _disconnect_old_client():
                        try:
                            if agent._client and agent._client.is_connected():
                                await agent._client.disconnect()
                        except Exception as e:
                            logger.warning(f"Error disconnecting old client for {agent_name}: {e}")
                    
                    # Schedule disconnect on the client's event loop
                    agent.execute(_disconnect_old_client())
                except Exception as e:
                    logger.warning(f"Error scheduling disconnect for {agent_name}: {e}")
                
                # Clear the client reference to prevent using it in wrong event loop
                agent._client = None
                agent._loop = None  # Clear cached loop when client is cleared
            
            # Update agent in registry
            register_telegram_agent(
                name=parsed["name"],
                phone=parsed["phone"],
                instructions=parsed["instructions"],
                role_prompt_names=parsed["role_prompt_names"],
                sticker_set_names=parsed.get("sticker_set_names") or [],
                explicit_stickers=parsed.get("explicit_stickers") or [],
                config_directory=agent.config_directory,
                timezone=parsed.get("timezone"),
                llm_name=parsed.get("llm_name"),
            )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating prompt for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/conversation-partners", methods=["GET"])
def api_get_conversation_partners(agent_name: str):
    """Get list of conversation partners for an agent."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        # Dictionary to store partners: {user_id: {"name": name, "date": date}}
        partners_dict = {}

        # 1. From curated memory files
        if agent.config_directory:
            memory_dir = (
                Path(agent.config_directory) / "agents" / agent_name / "memory"
            )
            if memory_dir.exists():
                for memory_file in memory_dir.glob("*.json"):
                    user_id = memory_file.stem
                    if user_id not in partners_dict:
                        partners_dict[user_id] = {"name": None, "date": None}

        # 2. From plan files
        plan_dir = Path(STATE_DIRECTORY) / agent_name / "memory"
        if plan_dir.exists():
            for plan_file in plan_dir.glob("*.json"):
                user_id = plan_file.stem
                if user_id not in partners_dict:
                    partners_dict[user_id] = {"name": None, "date": None}

        # 3. From existing Telegram conversations (if agent has client)
        # Use the agent's own Telegram client and event loop
        client = agent.client
        
        if not client:
            logger.info(f"Agent {agent_name} has no client - skipping Telegram conversation fetch")
        elif not client.is_connected():
            logger.info(f"Agent {agent_name} client is not connected - skipping Telegram conversation fetch")
        else:
            logger.info(f"Fetching Telegram conversations for agent {agent_name} using agent's client")
            telegram_partners = []  # Initialize before try block
            try:
                # Check if agent's event loop is accessible before creating coroutine
                # This prevents RuntimeWarning about unawaited coroutines if execute() fails
                try:
                    client_loop = agent._get_client_loop()
                    if not client_loop or not client_loop.is_running():
                        raise RuntimeError("Agent client event loop is not accessible or not running")
                except Exception as e:
                    logger.warning(f"Cannot fetch Telegram conversations - event loop check failed: {e}")
                    telegram_partners = []
                else:
                    async def _fetch_telegram_conversations():
                        """Fetch Telegram conversations - runs in agent's event loop via agent.execute()."""
                        telegram_partners = []
                        try:
                            # Use agent.client to get the client (already checked to be available and connected)
                            client = agent.client
                            # Iterate through dialogs - this runs in the client's event loop
                            async for dialog in client.iter_dialogs():
                                # Only include DMs (users), not groups or channels
                                if isinstance(dialog.entity, User):
                                    # Normalize peer ID
                                    try:
                                        dialog_id = dialog.id
                                        if hasattr(dialog_id, 'user_id'):
                                            dialog_id = dialog_id.user_id
                                        elif isinstance(dialog_id, int):
                                            pass  # Already an int
                                        else:
                                            dialog_id = int(dialog_id)
                                        user_id = str(normalize_peer_id(dialog_id))
                                    except Exception as e:
                                        logger.warning(f"Error normalizing peer ID for dialog {dialog.id}: {e}")
                                        continue
                                    # Get name from dialog.entity (already provided by iter_dialogs)
                                    # Avoid calling get_entity() to prevent GetContactsRequest flood
                                    user_name = None
                                    user_entity = dialog.entity
                                    
                                    # Try first_name/last_name first
                                    if hasattr(user_entity, "first_name") or hasattr(user_entity, "last_name"):
                                        first_name = getattr(user_entity, "first_name", None) or ""
                                        last_name = getattr(user_entity, "last_name", None) or ""
                                        if first_name or last_name:
                                            user_name = f"{first_name} {last_name}".strip()
                                    
                                    # Try username if we don't have a name yet
                                    if not user_name and hasattr(user_entity, "username") and user_entity.username:
                                        user_name = user_entity.username
                                    
                                    # Note: We intentionally don't call get_entity() here to avoid GetContactsRequest flood.
                                    # The dialog.entity from iter_dialogs() should contain sufficient information.
                                    # If name is missing, we'll just use None (user_id will still be displayed).
                                    
                                    # Normalize empty strings to None
                                    if user_name and isinstance(user_name, str):
                                        user_name = user_name.strip()
                                        if not user_name:
                                            user_name = None
                                    
                                    # Get most recent message date
                                    dialog_date = dialog.date if hasattr(dialog, 'date') and dialog.date else None
                                    
                                    telegram_partners.append({
                                        "user_id": user_id,
                                        "name": user_name,
                                        "date": dialog_date
                                    })
                        except Exception as e:
                            logger.warning(f"Error fetching Telegram conversations: {e}")
                        return telegram_partners

                    # Use agent.execute() to run the coroutine on the agent's event loop
                    telegram_partners = agent.execute(_fetch_telegram_conversations(), timeout=30.0)
                logger.info(f"Fetched {len(telegram_partners)} partners from Telegram for agent {agent_name}")
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "event loop" in error_msg or "no current event loop" in error_msg or "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Cannot fetch Telegram conversations: {e}")
                    telegram_partners = []
                else:
                    logger.warning(f"RuntimeError fetching Telegram conversations: {e}", exc_info=True)
                    telegram_partners = []
            except TimeoutError as e:
                logger.warning(f"Timeout fetching Telegram conversations for agent {agent_name}: {e}")
                telegram_partners = []
            except Exception as e:
                logger.warning(f"Error fetching Telegram conversations: {e}", exc_info=True)
                telegram_partners = []
            
            # Merge with existing partners (always runs, regardless of success or failure)
            for partner in telegram_partners:
                user_id = partner["user_id"]
                partner_name = partner.get("name")
                # Only use name if it's a non-empty string
                if partner_name and isinstance(partner_name, str) and partner_name.strip():
                    partner_name = partner_name.strip()
                else:
                    partner_name = None
                
                if user_id in partners_dict:
                    # Update name if we have a valid name from Telegram
                    if partner_name:
                        partners_dict[user_id]["name"] = partner_name
                    # Update date if we have a newer one
                    if partner["date"] and (not partners_dict[user_id]["date"] or partner["date"] > partners_dict[user_id]["date"]):
                        partners_dict[user_id]["date"] = partner["date"]
                else:
                    # Add new partner from Telegram
                    partners_dict[user_id] = {
                        "name": partner_name,
                        "date": partner["date"]
                    }

        # Convert to list, keeping datetime objects for sorting
        partner_list_with_dates = []
        for user_id, info in partners_dict.items():
            partner_list_with_dates.append({
                "user_id": user_id,
                "name": info["name"],
                "date_obj": info["date"]  # Keep datetime object for sorting
            })
        
        # Sort by date (most recent first), then by user_id for those without dates
        from datetime import datetime
        min_date = datetime(1970, 1, 1, tzinfo=UTC)
        partner_list_with_dates.sort(key=lambda x: (
            x["date_obj"] if x["date_obj"] else min_date,
            x["user_id"]
        ), reverse=True)
        
        # Convert to final list with ISO date strings for JSON
        partner_list = []
        for partner in partner_list_with_dates:
            date_str = partner["date_obj"].isoformat() if partner["date_obj"] else None
            partner_list.append({
                "user_id": partner["user_id"],
                "name": partner["name"],
                "date": date_str
            })

        return jsonify({"partners": partner_list})
    except Exception as e:
        logger.error(f"Error getting conversation partners for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/conversation-llm/<user_id>", methods=["GET"])
def api_get_conversation_llm(agent_name: str, user_id: str):
    """Get conversation-specific LLM for a user."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        conversation_llm = agent.get_channel_llm_model(channel_id)
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
        logger.error(f"Error getting conversation LLM for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/conversation-llm/<user_id>", methods=["PUT"])
def api_update_conversation_llm(agent_name: str, user_id: str):
    """Update conversation-specific LLM for a user."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        data = request.json
        llm_name = data.get("llm_name", "").strip()

        memory_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        agent_default_llm = agent._llm_name or get_default_llm()

        # If setting to agent default, remove the conversation-specific LLM
        if llm_name == agent_default_llm or not llm_name:
            if memory_file.exists():
                _, payload = load_property_entries(
                    memory_file, "plan", default_id_prefix="plan"
                )
                if payload and isinstance(payload, dict):
                    payload.pop("llm_model", None)
                    write_property_entries(
                        memory_file, "plan", payload.get("plan", []), payload=payload
                    )
        else:
            # Set conversation-specific LLM
            _, payload = load_property_entries(
                memory_file, "plan", default_id_prefix="plan"
            )
            if payload is None:
                payload = {}
            payload["llm_model"] = llm_name
            write_property_entries(
                memory_file, "plan", payload.get("plan", []), payload=payload
            )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating conversation LLM for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/plans/<user_id>", methods=["GET"])
def api_get_plans(agent_name: str, user_id: str):
    """Get plans for a conversation."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        plan_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")

        # Sort by created timestamp (newest first)
        plans.sort(key=lambda x: x.get("created", ""), reverse=True)

        return jsonify({"plans": plans})
    except MemoryStorageError as e:
        logger.error(f"Error loading plans for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error getting plans for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/plans/<user_id>/<plan_id>", methods=["PUT"])
def api_update_plan(agent_name: str, user_id: str, plan_id: str):
    """Update a plan entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        data = request.json
        content = data.get("content", "").strip()

        plan_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"

        def update_plan(entries, payload):
            for entry in entries:
                if entry.get("id") == plan_id:
                    entry["content"] = content
                    break
            return entries, payload

        mutate_property_entries(
            plan_file, "plan", default_id_prefix="plan", mutator=update_plan
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error updating plan {plan_id} for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/plans/<user_id>/<plan_id>", methods=["DELETE"])
def api_delete_plan(agent_name: str, user_id: str, plan_id: str):
    """Delete a plan entry."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        plan_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"

        def delete_plan(entries, payload):
            entries = [e for e in entries if e.get("id") != plan_id]
            return entries, payload

        mutate_property_entries(
            plan_file, "plan", default_id_prefix="plan", mutator=delete_plan
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting plan {plan_id} for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/plans/<user_id>", methods=["POST"])
def api_create_plan(agent_name: str, user_id: str):
    """Create a new plan entry."""
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
        
        if not content:
            return jsonify({"error": "Content is required"}), 400

        plan_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        
        import uuid
        from time_utils import normalize_created_string
        
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        created_value = normalize_created_string(None, agent)
        
        new_entry = {
            "id": plan_id,
            "content": content,
            "created": created_value,
            "origin": "puppetmaster"
        }

        def create_plan(entries, payload):
            entries.append(new_entry)
            return entries, payload

        mutate_property_entries(
            plan_file, "plan", default_id_prefix="plan", mutator=create_plan
        )

        return jsonify({"success": True, "plan": new_entry})
    except Exception as e:
        logger.error(f"Error creating plan for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


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

        data = request.json
        content = data.get("content", "").strip()
        min_message_id = data.get("min_message_id")
        max_message_id = data.get("max_message_id")

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
        
        if not content:
            return jsonify({"error": "Content is required"}), 400
        if min_message_id is None or max_message_id is None:
            return jsonify({"error": "min_message_id and max_message_id are required"}), 400

        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        
        import uuid
        from time_utils import normalize_created_string
        
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


def _get_highest_summarized_message_id_for_api(agent_name: str, channel_id: int) -> int | None:
    """
    Get the highest message ID that has been summarized (for use in Flask context).
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        
        highest_max_id = None
        for summary in summaries:
            max_id = summary.get("max_message_id")
            if max_id is not None:
                try:
                    max_id_int = int(max_id)
                    if highest_max_id is None or max_id_int > highest_max_id:
                        highest_max_id = max_id_int
                except (ValueError, TypeError):
                    pass
        return highest_max_id
    except Exception as e:
        logger.debug(f"Failed to get highest summarized message ID for {agent_name}/{channel_id}: {e}")
        return None


def _has_conversation_content_local(agent_name: str, channel_id: int) -> bool:
    """
    Check if a conversation has content by checking local files only (no Telegram API calls).
    
    Returns True if summaries exist or if the summary file exists (indicating conversation data).
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        if not summary_file.exists():
            return False
        
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        # If summaries exist, there's conversation content
        return len(summaries) > 0
    except Exception:
        return False


@agents_bp.route("/api/agents/<agent_name>/conversation-content-check", methods=["POST"])
def api_check_conversation_content_batch(agent_name: str):
    """
    Batch check which partners have conversation content (local files only, no Telegram API calls).
    
    Request body: {"user_ids": ["user_id1", "user_id2", ...]}
    Response: {"content_checks": {"user_id1": true, "user_id2": false, ...}}
    """
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        data = request.json or {}
        user_ids = data.get("user_ids", [])
        
        if not isinstance(user_ids, list):
            return jsonify({"error": "user_ids must be a list"}), 400

        content_checks = {}
        for user_id_str in user_ids:
            try:
                channel_id = int(user_id_str)
                content_checks[user_id_str] = _has_conversation_content_local(agent_name, channel_id)
            except (ValueError, TypeError):
                content_checks[user_id_str] = False

        return jsonify({"content_checks": content_checks})
    except Exception as e:
        logger.error(f"Error checking conversation content for {agent_name}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>", methods=["GET"])
def api_get_conversation(agent_name: str, user_id: str):
    """Get conversation history (unsummarized messages only) and summaries."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.client or not agent.client.is_connected():
            return jsonify({"error": "Agent client not connected"}), 503

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        # Get summaries
        summary_file = Path(STATE_DIRECTORY) / agent_name / "memory" / f"{channel_id}.json"
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))
        
        # Get highest summarized message ID to filter messages
        highest_summarized_id = _get_highest_summarized_message_id_for_api(agent_name, channel_id)

        # Get conversation history from Telegram
        # Check if agent's event loop is accessible before creating coroutine
        # This prevents RuntimeWarning about unawaited coroutines if execute() fails
        try:
            client_loop = agent._get_client_loop()
            if not client_loop or not client_loop.is_running():
                raise RuntimeError("Agent client event loop is not accessible or not running")
        except Exception as e:
            logger.warning(f"Cannot fetch conversation - event loop check failed: {e}")
            return jsonify({"error": "Agent client event loop is not available"}), 503
        
        # This is async, so we need to run it in the client's event loop
        async def _get_messages():
            try:
                # Use client.get_entity() directly since we're already in the client's event loop
                # This avoids event loop mismatch issues with agent.get_cached_entity()
                client = agent.client
                entity = await client.get_entity(channel_id)
                if not entity:
                    return []
                messages = []
                async for message in client.iter_messages(entity, limit=500):
                    # Filter out summarized messages
                    msg_id = int(message.id)
                    if highest_summarized_id is not None and msg_id <= highest_summarized_id:
                        continue  # Skip summarized messages
                    
                    from_id = getattr(message, "from_id", None)
                    sender_id = None
                    if from_id:
                        sender_id = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
                    is_from_agent = sender_id == agent.agent_id
                    text = message.text or ""
                    timestamp = message.date.isoformat() if hasattr(message, "date") and message.date else None
                    messages.append({
                        "id": str(message.id),
                        "text": text,
                        "sender_id": str(sender_id) if sender_id else None,
                        "is_from_agent": is_from_agent,
                        "timestamp": timestamp,
                    })
                return list(reversed(messages))  # Return in chronological order
            except Exception as e:
                logger.error(f"Error fetching messages: {e}")
                return []

        # Use agent.execute() to run the coroutine on the agent's event loop
        try:
            messages = agent.execute(_get_messages(), timeout=30.0)
            return jsonify({"messages": messages, "summaries": summaries})
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "not authenticated" in error_msg or "not running" in error_msg:
                logger.warning(f"Agent {agent_name} client loop issue: {e}")
                return jsonify({"error": "Agent client loop is not available"}), 503
            else:
                logger.error(f"Error fetching conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except TimeoutError:
            logger.warning(f"Timeout fetching conversation for agent {agent_name}, user {user_id}")
            return jsonify({"error": "Timeout fetching conversation"}), 504
        except Exception as e:
            logger.error(f"Error fetching conversation: {e}")
            return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error getting conversation for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


# Translation JSON schema for message translation
_TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The message ID from the input"
                    },
                    "translated_text": {
                        "type": "string",
                        "description": "The English translation of the message text"
                    }
                },
                "required": ["message_id", "translated_text"],
                "additionalProperties": False
            }
        }
    },
    "required": ["translations"],
    "additionalProperties": False
}


@agents_bp.route("/api/agents/<agent_name>/conversation/<user_id>/translate", methods=["POST"])
def api_translate_conversation(agent_name: str, user_id: str):
    """Translate unsummarized messages into English using the media LLM."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        # Get messages from request
        data = request.json
        messages = data.get("messages", [])
        if not messages:
            return jsonify({"error": "No messages provided"}), 400

        # Check if agent's event loop is accessible
        try:
            client_loop = agent._get_client_loop()
            if not client_loop or not client_loop.is_running():
                raise RuntimeError("Agent client event loop is not accessible or not running")
        except Exception as e:
            logger.warning(f"Cannot translate conversation - event loop check failed: {e}")
            return jsonify({"error": "Agent client event loop is not available"}), 503

        # Get media LLM
        try:
            media_llm = get_media_llm()
        except Exception as e:
            logger.error(f"Failed to get media LLM: {e}")
            return jsonify({"error": "Media LLM not available"}), 503

        # Build translation prompt with messages as structured JSON
        # This avoids issues with unescaped quotes/newlines in message text
        messages_for_prompt = []
        for msg in messages:
            msg_id = msg.get("id", "")
            msg_text = msg.get("text", "")
            if msg_text:
                messages_for_prompt.append({
                    "message_id": str(msg_id),
                    "text": msg_text
                })
        
        # Convert to JSON string for the prompt (properly escaped)
        import json as json_module
        messages_json = json_module.dumps(messages_for_prompt, ensure_ascii=False, indent=2)
        
        translation_prompt = f"""Translate the following conversation messages into English. 
Preserve the message structure and return a JSON object with translations.

Input messages (as JSON):
{messages_json}

Return a JSON object with this structure:
{{
  "translations": [
    {{"message_id": "123", "translated_text": "English translation here"}},
    ...
  ]
}}

Translate all messages provided, maintaining the order and message IDs. Ensure all JSON is properly formatted."""

        # This is async, so we need to run it in the client's event loop
        async def _translate_messages():
            try:
                # Use media LLM's query_structured with custom schema
                # We need to modify the Gemini LLM to accept custom schemas
                # For now, let's use a simpler approach with direct API call
                from llm.gemini import GeminiLLM
                import copy
                
                from llm.gemini import GeminiLLM
                from google.genai.types import GenerateContentConfig
                import asyncio
                
                if isinstance(media_llm, GeminiLLM):
                    # Build contents for Gemini
                    contents = [{
                        "role": "user",
                        "parts": [{"text": translation_prompt}]
                    }]
                    
                    # Use internal method with custom schema
                    client = getattr(media_llm, "client", None)
                    if not client:
                        raise RuntimeError("Media LLM client not initialized")
                    
                    config = GenerateContentConfig(
                        system_instruction="You are a translation assistant. Translate messages into English and return JSON.",
                        safety_settings=media_llm.safety_settings,
                        response_mime_type="application/json",
                        response_json_schema=copy.deepcopy(_TRANSLATION_SCHEMA),
                    )
                    
                    response = await asyncio.to_thread(
                        client.models.generate_content,
                        model=media_llm.model_name,
                        contents=contents,
                        config=config,
                    )
                    
                    # Use the same text extraction helper as GeminiLLM for consistency
                    from llm.gemini import _extract_response_text
                    result_text = _extract_response_text(response)
                    
                    if result_text:
                        # Parse JSON response with better error handling
                        try:
                            result = json_lib.loads(result_text)
                            translations = result.get("translations", [])
                            if isinstance(translations, list):
                                return translations
                            else:
                                logger.warning(f"Translations is not a list: {type(translations)}")
                                return []
                        except json_lib.JSONDecodeError as e:
                            logger.error(f"JSON decode error in translation response: {e}")
                            logger.debug(f"Response text (first 500 chars): {result_text[:500]}")
                            # Try to extract JSON from markdown code blocks if present
                            import re
                            json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', result_text, re.DOTALL)
                            if json_match:
                                try:
                                    result = json_lib.loads(json_match.group(1))
                                    return result.get("translations", [])
                                except json_lib.JSONDecodeError:
                                    pass
                            # Try to find JSON object in the text (more lenient)
                            json_match = re.search(r'\{[^{}]*"translations"[^{}]*\[.*?\]\s*\}', result_text, re.DOTALL)
                            if json_match:
                                try:
                                    result = json_lib.loads(json_match.group(0))
                                    return result.get("translations", [])
                                except json_lib.JSONDecodeError:
                                    pass
                            return []
                    
                    return []
                else:
                    # For non-Gemini LLMs, use a simpler approach
                    # This would need to be implemented based on the LLM type
                    raise NotImplementedError(f"Translation not implemented for LLM type: {type(media_llm)}")
            except Exception as e:
                logger.error(f"Error translating messages: {e}")
                return []

        # Use agent.execute() to run the coroutine on the agent's event loop
        try:
            translations = agent.execute(_translate_messages(), timeout=60.0)
            
            # Convert to dict for easy lookup
            translation_dict = {t["message_id"]: t["translated_text"] for t in translations}
            
            return jsonify({"translations": translation_dict})
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "not authenticated" in error_msg or "not running" in error_msg:
                logger.warning(f"Agent {agent_name} client loop issue: {e}")
                return jsonify({"error": "Agent client loop is not available"}), 503
            else:
                logger.error(f"Error translating conversation: {e}")
                return jsonify({"error": str(e)}), 500
        except TimeoutError:
            logger.warning(f"Timeout translating conversation for agent {agent_name}, user {user_id}")
            return jsonify({"error": "Timeout translating conversation"}), 504
        except Exception as e:
            logger.error(f"Error translating conversation: {e}")
            return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.error(f"Error translating conversation for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/api/agents/<agent_name>/xsend/<user_id>", methods=["POST"])
def api_xsend(agent_name: str, user_id: str):
    """Create an xsend task to trigger a received task on another channel."""
    try:
        agent = get_agent_by_name(agent_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_name}' not found"}), 404

        if not agent.agent_id:
            return jsonify({"error": "Agent not authenticated"}), 400

        try:
            channel_id = int(user_id)
        except ValueError:
            return jsonify({"error": "Invalid user ID"}), 400

        data = request.json
        intent = data.get("intent", "").strip()

        # Get work queue singleton
        import os
        state_path = os.path.join(STATE_DIRECTORY, "work_queue.json")
        work_queue = WorkQueue.get_instance()

        # Create xsend task by inserting a received task with xsend_intent
        # This is async, so we need to run it on the agent's event loop
        async def _create_xsend():
            await insert_received_task_for_conversation(
                recipient_id=agent.agent_id,
                channel_id=str(channel_id),
                xsend_intent=intent if intent else None,
            )
            # Save work queue back to state file
            work_queue.save(state_path)

        # Use agent.execute() to run the coroutine on the agent's event loop
        try:
            agent.execute(_create_xsend(), timeout=30.0)
            return jsonify({"success": True, "message": "XSend task created successfully"})
        except RuntimeError as e:
            error_msg = str(e).lower()
            if "not authenticated" in error_msg or "not running" in error_msg:
                logger.warning(f"Agent {agent_name} client loop issue: {e}")
                return jsonify({"error": "Agent client loop is not available"}), 503
            else:
                logger.error(f"Error creating xsend task: {e}")
                return jsonify({"error": str(e)}), 500
        except TimeoutError:
            logger.warning(f"Timeout creating xsend task for agent {agent_name}, user {user_id}")
            return jsonify({"error": "Timeout creating xsend task"}), 504
    except Exception as e:
        logger.error(f"Error creating xsend task for {agent_name}/{user_id}: {e}")
        return jsonify({"error": str(e)}), 500


