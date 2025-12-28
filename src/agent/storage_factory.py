# agent/storage_factory.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Storage factory for creating the appropriate storage backend.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from config import STORAGE_BACKEND

if TYPE_CHECKING:
    from agent.storage_impl import AgentStorage

logger = logging.getLogger(__name__)


def create_storage(
    agent_config_name: str,
    agent_telegram_id: int | None,
    config_directory: Path | None,
    state_directory: Path,
) -> "AgentStorage":
    """
    Create the appropriate storage backend based on configuration.
    
    Args:
        agent_config_name: The agent's config file name (without .md extension)
        agent_telegram_id: The agent's Telegram ID (None if not authenticated yet)
        config_directory: Optional config directory path (for curated memories)
        state_directory: State directory path (for filesystem fallback)
    
    Returns:
        AgentStorage instance (filesystem or MySQL)
    """
    # Use MySQL if:
    # 1. Storage backend is set to 'mysql'
    # 2. Agent has a telegram ID (authenticated)
    # 3. MySQL is properly configured
    use_mysql = (
        STORAGE_BACKEND == "mysql"
        and agent_telegram_id is not None
    )
    
    if use_mysql:
        try:
            from agent.storage_mysql import AgentStorageMySQL
            from config import MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD
            
            # Verify MySQL is configured
            if not all([MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD]):
                logger.warning(
                    f"[{agent_config_name}] MySQL storage requested but configuration incomplete. "
                    "Falling back to filesystem storage."
                )
                use_mysql = False
            else:
                return AgentStorageMySQL(
                    agent_config_name=agent_config_name,
                    agent_telegram_id=agent_telegram_id,
                    config_directory=config_directory,
                    state_directory=state_directory,  # Still needed for config memory
                )
        except ImportError as e:
            logger.warning(
                f"[{agent_config_name}] Failed to import MySQL storage: {e}. "
                "Falling back to filesystem storage."
            )
            use_mysql = False
        except Exception as e:
            logger.warning(
                f"[{agent_config_name}] Failed to initialize MySQL storage: {e}. "
                "Falling back to filesystem storage."
            )
            use_mysql = False
    
    # Fallback to filesystem storage
    from agent.storage_impl import AgentStorage
    return AgentStorage(
        agent_config_name=agent_config_name,
        config_directory=config_directory,
        state_directory=state_directory,
    )

