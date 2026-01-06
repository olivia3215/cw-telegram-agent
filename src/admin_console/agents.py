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

# Load and register profile routes
profile_module = _load_submodule("profile")
profile_module.register_profile_routes(agents_bp)


def _sort_agents_by_name(agent_list: list[dict[str, Any]]) -> None:
    """Sort a list of agent dictionaries alphabetically by name (case-insensitive).
    
    Args:
        agent_list: List of agent dictionaries, each containing at least a "name" key.
                    The list is modified in-place.
    """
    agent_list.sort(key=lambda x: x["name"].lower())


def _build_agent_id_by_config_name(agent_config_names: list[str], agents: list) -> dict[str, int]:
    """
    Build a mapping from agent config names to agent IDs for authenticated agents.
    
    Args:
        agent_config_names: List of agent config names to include in the mapping
        agents: List of agent objects
        
    Returns:
        Dictionary mapping config_name -> agent_id for authenticated agents
        whose config_name is in the provided list
    """
    agent_id_by_config_name = {}
    for agent in agents:
        if agent.config_name in agent_config_names and agent.is_authenticated:
            agent_id_by_config_name[agent.config_name] = agent.agent_id
    return agent_id_by_config_name


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
    agent_id_by_config_name = _build_agent_id_by_config_name(agent_config_names, agents)
    
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


def _agents_with_conversation_llm_overrides(agent_config_names: list[str], agents: list) -> set[str]:
    """
    Check which agents have conversation-specific LLM overrides using MySQL bulk query.
    
    Args:
        agent_config_names: List of agent config names to check
        agents: List of agent objects (to map config_name to agent_id)
        
    Returns:
        Set of agent config names that have conversation LLM overrides
    """
    # Map agent config names to agent IDs
    agent_id_by_config_name = _build_agent_id_by_config_name(agent_config_names, agents)
    
    if not agent_id_by_config_name:
        return set()
    
    # Bulk query to check which agents have conversation LLM overrides
    try:
        from db import conversation_llm
        agent_ids_with_overrides = conversation_llm.agents_with_conversation_llm_overrides(
            list(agent_id_by_config_name.values())
        )
        
        # Map back to config names
        config_names_with_overrides = {
            config_name
            for config_name, agent_id in agent_id_by_config_name.items()
            if agent_id in agent_ids_with_overrides
        }
        return config_names_with_overrides
    except Exception as e:
        logger.debug(f"Error checking conversation LLM overrides in MySQL: {e}")
        return set()


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
        
        # Check which agents have curated memories and conversation LLM overrides (MySQL bulk queries)
        agent_config_names = [agent.config_name for agent in agents if agent.config_name]
        try:
            agents_with_curated_memories_set = _agents_with_curated_memories(agent_config_names, agents)
        except Exception as e:
            logger.debug(f"Error checking curated memories: {e}")
            agents_with_curated_memories_set = set()
        
        try:
            agents_with_conversation_llm_set = _agents_with_conversation_llm_overrides(agent_config_names, agents)
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
            
            # Check if agent has curated memories and conversation LLM overrides (MySQL-based)
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


@agents_bp.route("/api/agents/recent-conversations", methods=["GET"])
def api_recent_conversations():
    """Get list of 20 most recent conversations from agent_activity table."""
    try:
        from db import agent_activity
        from agent import get_agent_for_id
        from utils.telegram import get_channel_name
        
        # Get recent activities from database
        activities = agent_activity.get_recent_activity(limit=20)
        
        recent_conversations = []
        for activity in activities:
            agent_telegram_id = activity["agent_telegram_id"]
            channel_telegram_id = activity["channel_telegram_id"]
            last_send_time = activity["last_send_time"]
            
            # Filter out Telegram system user (777000) - defense in depth
            from config import TELEGRAM_SYSTEM_USER_ID
            if channel_telegram_id == TELEGRAM_SYSTEM_USER_ID:
                continue
            
            # Get agent instance directly by telegram ID (more reliable than config_name)
            agent = get_agent_for_id(agent_telegram_id)
            if not agent or not agent.is_authenticated or not agent.client:
                # Skip if agent not available or not authenticated
                continue
            
            # Get config_name from the agent instance
            agent_config_name = agent.config_name
            if not agent_config_name:
                # Skip if agent doesn't have a config_name
                continue
            
            # Get channel name (requires async, so use agent.execute)
            try:
                async def _get_channel_name():
                    try:
                        return await get_channel_name(agent, channel_telegram_id)
                    except Exception as e:
                        logger.debug(f"Error getting channel name for {channel_telegram_id}: {e}")
                        return None
                
                channel_name = agent.execute(_get_channel_name(), timeout=5.0)
                
                if channel_name:
                    recent_conversations.append({
                        "agent_config_name": agent_config_name,
                        "agent_name": agent.name,
                        "channel_id": str(channel_telegram_id),
                        "channel_name": channel_name,
                        "last_send_time": last_send_time,
                    })
            except Exception as e:
                logger.debug(f"Error resolving channel name for agent {agent_telegram_id}, channel {channel_telegram_id}: {e}")
                # Skip this conversation if we can't get the channel name
                continue
        
        return jsonify({"conversations": recent_conversations})
    except Exception as e:
        logger.error(f"Error getting recent conversations: {e}")
        return jsonify({"error": str(e)}), 500
