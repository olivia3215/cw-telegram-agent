# admin_console/agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent management routes for the admin console.
"""

import json
import logging
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path

# Import asyncio only when needed to avoid event loop issues in Flask threads
try:
    import asyncio
except ImportError:
    asyncio = None

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon.tl.types import User, Chat, Channel  # pyright: ignore[reportMissingImports]

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
from utils import normalize_peer_id
from admin_console.helpers import (
    get_agent_by_name,
    get_default_llm,
    get_available_llms,
    get_work_queue,
)
from register_agents import register_all_agents, all_agents as get_all_agents
from handlers.received import _format_message_reactions, trigger_summarization_directly
from telepathic import TELEPATHIC_PREFIXES
from media.media_injector import format_message_for_prompt
from media.media_source import get_default_media_source_chain
from telegram_download import download_media_bytes
from telegram_media import iter_media_parts
from flask import Response
from media.mime_utils import detect_mime_type_from_bytes

logger = logging.getLogger(__name__)

# Create agents blueprint
agents_bp = Blueprint("agents", __name__)

# Import and register submodule routes
# Use importlib to load from agents/ subdirectory (avoiding conflict with agents.py module name)
import importlib.util
from pathlib import Path

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


# Memory routes moved to admin_console/agents/memory.py


# Intention routes moved to admin_console/agents/intentions.py


# Configuration routes moved to admin_console/agents/configuration.py
# Conversation LLM routes moved to admin_console/agents/conversation_llm.py


# Plan routes moved to admin_console/agents/plans.py
# Summary routes moved to admin_console/agents/summaries.py


# Conversation routes moved to admin_console/agents/conversation.py


