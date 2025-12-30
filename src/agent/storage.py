# agent/storage.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Memory and storage loading for Agent.
"""

import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent.storage_factory import create_storage

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent
    from agent.storage_mysql import AgentStorageMySQL


class AgentStorageMixin:
    """Mixin providing memory and storage loading capabilities."""

    name: str
    config_directory: str | None
    _storage_obj: "AgentStorageMySQL | None"

    @property
    def _storage(self):
        """
        Get or create the AgentStorageMySQL for this agent.
        
        Returns:
            AgentStorageMySQL instance
        
        Raises:
            ValueError: If agent_telegram_id is None (agent not authenticated)
            RuntimeError: If MySQL configuration is incomplete
        """
        if self._storage_obj is None:
            from config import STATE_DIRECTORY  # Import dynamically to allow patching in tests
            config_dir = Path(self.config_directory) if self.config_directory else None
            state_dir = Path(STATE_DIRECTORY)
            # Get agent_telegram_id if available (may be None if not authenticated yet)
            agent_telegram_id = getattr(self, "agent_id", None)
            self._storage_obj = create_storage(
                agent_config_name=self.config_name,
                agent_telegram_id=agent_telegram_id,
                config_directory=config_dir,
                state_directory=state_dir,
            )
        return self._storage_obj

    def _load_intention_content(self) -> str:
        """Load agent-specific global intentions content."""
        return self._storage.load_intention_content()

    def _load_memory_content(self, channel_id: int) -> str:
        """Load agent-specific global memory content."""
        return self._storage.load_memory_content(channel_id)

    def _load_config_memory(self, user_id: int) -> str:
        """Load curated memory from config directory for a specific user."""
        return self._storage.load_config_memory(user_id)

    def _load_state_memory(self) -> str:
        """Load agent-specific global episodic memory from state directory."""
        return self._storage.load_state_memory()

    def _load_plan_content(self, channel_id: int) -> str:
        """Load channel-specific plan content from state directory."""
        return self._storage.load_plan_content(channel_id)

    async def _load_summary_content(self, channel_id: int, json_format: bool = False, include_metadata: bool = False) -> str:
        """Load channel-specific summary content from state directory."""
        return self._storage.load_summary_content(channel_id, json_format=json_format, include_metadata=include_metadata)

    def get_channel_llm_model(self, channel_id: int) -> str | None:
        """Get the LLM model name for a specific channel from the channel memory file."""
        return self._storage.get_channel_llm_model(channel_id)

    def _load_schedule(self) -> dict | None:
        """
        Load agent's schedule from MySQL with caching.
        
        The schedule is cached in memory and only reloaded if:
        - Cache is empty (first load)
        - Cache was explicitly invalidated
        
        Returns a deep copy of the schedule to prevent cache mutation.
        If _save_schedule() fails, the cache remains unchanged and will be
        reloaded from MySQL on the next access.
        
        Returns:
            Deep copy of schedule dictionary or None if schedule doesn't exist
        """
        # Check if cache is valid
        if self._schedule_cache is not None:
            # Cache is valid, return a deep copy to prevent cache mutation
            # If _save_schedule() fails, the cache remains unchanged
            return copy.deepcopy(self._schedule_cache)
        
        # Load from MySQL
        schedule = self._storage.load_schedule()
        
        # Update cache
        self._schedule_cache = schedule
        
        # Return a deep copy to prevent cache mutation
        # If _save_schedule() fails, the cache remains unchanged
        return copy.deepcopy(schedule) if schedule is not None else None

    def _save_schedule(self, schedule: dict) -> None:
        """
        Save agent's schedule to MySQL and invalidate cache.
        
        Args:
            schedule: Schedule dictionary to save
        """
        self._storage.save_schedule(schedule)
        
        # Invalidate cache - it will be reloaded on next access
        self._schedule_cache = None
