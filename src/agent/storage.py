# agent/storage.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Memory and storage loading for Agent.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from storage.agent_storage import AgentStorage

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent


class AgentStorageMixin:
    """Mixin providing memory and storage loading capabilities."""

    name: str
    config_directory: str | None
    _storage_obj: AgentStorage | None

    @property
    def _storage(self):
        """
        Get or create the AgentStorage for this agent.
        
        Returns:
            AgentStorage instance
        """
        if self._storage_obj is None:
            from config import STATE_DIRECTORY  # Import dynamically to allow patching in tests
            config_dir = Path(self.config_directory) if self.config_directory else None
            state_dir = Path(STATE_DIRECTORY)
            self._storage_obj = AgentStorage(
                agent_name=self.name,
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

    def _load_summary_content(self, channel_id: int, json_format: bool = False) -> str:
        """Load channel-specific summary content from state directory."""
        return self._storage.load_summary_content(channel_id, json_format=json_format)

    def get_channel_llm_model(self, channel_id: int) -> str | None:
        """Get the LLM model name for a specific channel from the channel memory file."""
        return self._storage.get_channel_llm_model(channel_id)

