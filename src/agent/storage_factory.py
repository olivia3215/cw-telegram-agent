# src/agent/storage_factory.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Storage factory for creating MySQL storage backend.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from utils.formatting import format_log_prefix_resolved

if TYPE_CHECKING:
    from agent.storage_mysql import AgentStorageMySQL

logger = logging.getLogger(__name__)


def create_storage(
    agent_config_name: str,
    agent_telegram_id: int | None,
    config_directory: Path | None,
    state_directory: Path,
) -> "AgentStorageMySQL":
    """
    Create MySQL storage backend.
    
    Args:
        agent_config_name: The agent's config file name (without .md extension)
        agent_telegram_id: The agent's Telegram ID (None if not authenticated yet)
        config_directory: Optional config directory path (for notes)
        state_directory: State directory path (for config memory and channel metadata)
    
    Returns:
        AgentStorageMySQL instance
    
    Raises:
        ValueError: If agent_telegram_id is None or invalid
        RuntimeError: If MySQL configuration is incomplete (checked at startup in db.connection)
    """
    if agent_telegram_id is None:
        raise ValueError(
            f"{format_log_prefix_resolved(agent_config_name, None)} Cannot create MySQL storage: agent_telegram_id is None. "
            "Agent must be authenticated before storage can be created."
        )
    
    try:
        from agent.storage_mysql import AgentStorageMySQL
        
        # MySQL configuration is checked at startup in db.connection._init_connection_pool()
        return AgentStorageMySQL(
            agent_config_name=agent_config_name,
            agent_telegram_id=agent_telegram_id,
            config_directory=config_directory,
            state_directory=state_directory,  # Still needed for config memory and channel metadata
        )
    except ImportError as e:
        raise RuntimeError(
            f"{format_log_prefix_resolved(agent_config_name, None)} Failed to import MySQL storage: {e}"
        ) from e

