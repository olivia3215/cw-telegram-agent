# agent/storage_factory.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Storage factory for creating MySQL storage backend.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

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
        config_directory: Optional config directory path (for curated memories)
        state_directory: State directory path (for config memory and channel metadata)
    
    Returns:
        AgentStorageMySQL instance
    
    Raises:
        ValueError: If agent_telegram_id is None or invalid
        RuntimeError: If MySQL configuration is incomplete
    """
    if agent_telegram_id is None:
        raise ValueError(
            f"[{agent_config_name}] Cannot create MySQL storage: agent_telegram_id is None. "
            "Agent must be authenticated before storage can be created."
        )
    
    try:
        from agent.storage_mysql import AgentStorageMySQL
        from config import MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD
        
        # Verify MySQL is configured
        if not all([MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD]):
            raise RuntimeError(
                f"[{agent_config_name}] MySQL configuration incomplete. "
                "Please set CINDY_AGENT_MYSQL_DATABASE, CINDY_AGENT_MYSQL_USER, and CINDY_AGENT_MYSQL_PASSWORD."
            )
        
        return AgentStorageMySQL(
            agent_config_name=agent_config_name,
            agent_telegram_id=agent_telegram_id,
            config_directory=config_directory,
            state_directory=state_directory,  # Still needed for config memory and channel metadata
        )
    except ImportError as e:
        raise RuntimeError(
            f"[{agent_config_name}] Failed to import MySQL storage: {e}"
        ) from e

