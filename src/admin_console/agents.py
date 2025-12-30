# admin_console/agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent management routes for the admin console.
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]
from typing import Any

from register_agents import register_all_agents, all_agents as get_all_agents

logger = logging.getLogger(__name__)

# Create agents blueprint
agents_bp = Blueprint("agents", __name__)

# Import and register submodule routes
# Use importlib to load from agents/ subdirectory (avoiding conflict with agents.py module name)
import importlib.util

def _load_submodule(module_name: str):
    """Load a submodule from agents/ subdirectory."""
    module_path = Path(__file__).parent / "agents" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"admin_console_agents_{module_name}", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Load and register partner routes
partners_module = _load_submodule("partners")
partners_module.register_partner_routes(agents_bp)

# Load and register memory routes
memory_module = _load_submodule("memory")
memory_module.register_memory_routes(agents_bp)

# Load and register intention routes
intentions_module = _load_submodule("intentions")
intentions_module.register_intention_routes(agents_bp)

# Load and register plan routes
plans_module = _load_submodule("plans")
plans_module.register_plan_routes(agents_bp)

# Load and register summary routes
summaries_module = _load_submodule("summaries")
summaries_module.register_summary_routes(agents_bp)

# Load and register configuration routes
configuration_module = _load_submodule("configuration")
configuration_module.register_configuration_routes(agents_bp)

# Load and register conversation LLM routes
conversation_llm_module = _load_submodule("conversation_llm")
conversation_llm_module.register_conversation_llm_routes(agents_bp)

# Load and register conversation routes
conversation_module = _load_submodule("conversation")
conversation_module.register_conversation_routes(agents_bp)

# Load and register login routes
login_module = _load_submodule("login")
login_module.register_login_routes(agents_bp)


def _sort_agents_by_name(agent_list: list[dict[str, Any]]) -> None:
    """Sort a list of agent dictionaries alphabetically by name (case-insensitive).
    
    Args:
        agent_list: List of agent dictionaries, each containing at least a "name" key.
                    The list is modified in-place.
    """
    agent_list.sort(key=lambda x: x["name"].lower())


def _agents_with_curated_memories(agent_config_names: list[str], agents: list) -> set[str]:
    """
    Check which agents have curated memories using MySQL bulk query.
    
    Args:
        agent_config_names: List of agent config names to check
        agents: List of agent objects (to map config_name to agent_id)
        
    Returns:
        Set of agent config names that have curated memories
    """
    # Map agent config names to agent IDs
    agent_id_by_config_name = {}
    for agent in agents:
        if agent.config_name in agent_config_names and agent.agent_id is not None:
            agent_id_by_config_name[agent.config_name] = agent.agent_id
    
    if not agent_id_by_config_name:
        return set()
    
    # Bulk query to check which agents have curated memories
    try:
        from db import curated_memories as db_curated_memories
        agent_ids_with_memories = db_curated_memories.agents_with_curated_memories(list(agent_id_by_config_name.values()))
        
        # Map back to config names
        config_names_with_memories = {
            config_name
            for config_name, agent_id in agent_id_by_config_name.items()
            if agent_id in agent_ids_with_memories
        }
        return config_names_with_memories
    except Exception as e:
        logger.debug(f"Error checking curated memories in MySQL: {e}")
        return set()


def _agents_with_conversation_llm_overrides(agent_config_names: list[str]) -> set[str]:
    """
    Check which agents have conversation-specific LLM overrides by scanning state directories.
    
    Conversation LLM overrides are stored in {state_dir}/{agent_name}/memory/{channel_id}.json
    with an 'llm_model' property in the payload.
    
    Args:
        agent_config_names: List of agent config names to check
        
    Returns:
        Set of agent config names that have conversation LLM overrides
    """
    from config import STATE_DIRECTORY
    from memory_storage import load_property_entries
    
    agents_with_overrides = set()
    agent_config_names_set = set(agent_config_names)
    state_path = Path(STATE_DIRECTORY)
    
    if not state_path.exists():
        return agents_with_overrides
    
    # Check each agent's memory directory
    for agent_config_name in agent_config_names_set:
        agent_memory_dir = state_path / agent_config_name / "memory"
        if not agent_memory_dir.exists() or not agent_memory_dir.is_dir():
            continue
        
        # Check memory files for llm_model property
        for memory_file in agent_memory_dir.glob("*.json"):
            try:
                _, payload = load_property_entries(memory_file, "plan", default_id_prefix="plan")
                if payload and isinstance(payload, dict):
                    llm_model = payload.get("llm_model")
                    if llm_model and isinstance(llm_model, str) and llm_model.strip():
                        agents_with_overrides.add(agent_config_name)
                        break  # Found one override, no need to check more files
            except Exception:
                # Skip files that can't be parsed
                continue
    
    return agents_with_overrides


@agents_bp.route("/api/agents", methods=["GET"])
def api_agents():
    """Get list of all agents."""
    try:
        from config import CONFIG_DIRECTORIES
        from admin_console.docs import resolve_docs_path
        
        register_all_agents()
        agents = list(get_all_agents(include_disabled=True))
        
        # Bulk queries to check which agents have plans, memories, and intentions
        agent_ids_with_agent_id = [agent.agent_id for agent in agents if agent.agent_id is not None]
        if agent_ids_with_agent_id:
            try:
                from db.plans import agents_with_plans
                agents_with_plans_set = agents_with_plans(agent_ids_with_agent_id)
            except Exception as e:
                logger.debug(f"Error checking plans in MySQL: {e}")
                agents_with_plans_set = set()
            
            try:
                from db.memories import agents_with_memories
                agents_with_memories_set = agents_with_memories(agent_ids_with_agent_id)
            except Exception as e:
                logger.debug(f"Error checking memories in MySQL: {e}")
                agents_with_memories_set = set()
            
            try:
                from db.intentions import agents_with_intentions
                agents_with_intentions_set = agents_with_intentions(agent_ids_with_agent_id)
            except Exception as e:
                logger.debug(f"Error checking intentions in MySQL: {e}")
                agents_with_intentions_set = set()
        else:
            agents_with_plans_set = set()
            agents_with_memories_set = set()
            agents_with_intentions_set = set()
        
        # Check which agents have curated memories (MySQL bulk query) and conversation LLM overrides (filesystem-based)
        agent_config_names = [agent.config_name for agent in agents if agent.config_name]
        try:
            agents_with_curated_memories_set = _agents_with_curated_memories(agent_config_names, agents)
        except Exception as e:
            logger.debug(f"Error checking curated memories: {e}")
            agents_with_curated_memories_set = set()
        
        try:
            agents_with_conversation_llm_set = _agents_with_conversation_llm_overrides(agent_config_names)
        except Exception as e:
            logger.debug(f"Error checking conversation LLM overrides: {e}")
            agents_with_conversation_llm_set = set()
        
        agent_list = []
        for agent in agents:
            # Check if agent has documents
            # Check all config directories for this agent's docs (agent can have docs in any config dir)
            has_documents = False
            if agent.config_name:
                for config_dir in CONFIG_DIRECTORIES:
                    try:
                        docs_path = resolve_docs_path(config_dir, agent.config_name)
                        if docs_path.exists() and docs_path.is_dir():
                            # Check if there are any .md files
                            md_files = list(docs_path.glob("*.md"))
                            if md_files:
                                has_documents = True
                                logger.debug(f"Agent {agent.name} ({agent.config_name}) has {len(md_files)} documents in {docs_path}")
                                break
                    except Exception as e:
                        # If path resolution fails, skip this config directory
                        logger.debug(f"Failed to check docs path for {agent.config_name} in {config_dir}: {e}")
                        continue
            
            # Check if agent has plans, memories, and intentions (from bulk queries)
            has_plans = agent.agent_id is not None and agent.agent_id in agents_with_plans_set
            has_memories = agent.agent_id is not None and agent.agent_id in agents_with_memories_set
            has_intentions = agent.agent_id is not None and agent.agent_id in agents_with_intentions_set
            
            # Check if agent has curated memories and conversation LLM overrides (filesystem-based)
            has_curated_memories = agent.config_name in agents_with_curated_memories_set
            has_conversation_llm = agent.config_name in agents_with_conversation_llm_set
            
            agent_list.append({
                "name": agent.name,
                "config_name": agent.config_name,
                "phone": agent.phone,
                "agent_id": agent.agent_id if agent.agent_id is not None else None,
                "telegram_username": agent.telegram_username if agent.telegram_username else None,
                "config_directory": agent.config_directory if agent.config_directory else None,
                "is_disabled": agent.is_disabled,
                "has_documents": has_documents,
                "has_plans": has_plans,
                "has_memories": has_memories,
                "has_intentions": has_intentions,
                "has_curated_memories": has_curated_memories,
                "has_conversation_llm": has_conversation_llm
            })
        
        _sort_agents_by_name(agent_list)
        return jsonify({"agents": agent_list})
    except Exception as e:
        logger.error(f"Error getting agents list: {e}")
        return jsonify({"error": str(e)}), 500


# Memory routes moved to admin_console/agents/memory.py


# Intention routes moved to admin_console/agents/intentions.py


# Configuration routes moved to admin_console/agents/configuration.py
# Conversation LLM routes moved to admin_console/agents/conversation_llm.py


# Plan routes moved to admin_console/agents/plans.py
# Summary routes moved to admin_console/agents/summaries.py


# Conversation routes moved to admin_console/agents/conversation.py
