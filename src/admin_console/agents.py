# admin_console/agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent management routes for the admin console.
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

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

@agents_bp.route("/api/agents", methods=["GET"])
def api_agents():
    """Get list of all agents."""
    try:
        register_all_agents()
        agents = list(get_all_agents(include_disabled=True))
        agent_list = [
            {
                "name": agent.name,
                "config_name": agent.config_name,
                "phone": agent.phone,
                "agent_id": agent.agent_id if agent.agent_id is not None else None,
                "telegram_username": agent.telegram_username if agent.telegram_username else None,
                "config_directory": agent.config_directory if agent.config_directory else None,
                "is_disabled": agent.is_disabled
            }
            for agent in agents
        ]
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
