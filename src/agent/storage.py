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

    async def _load_summary_content(self, channel_id: int, json_format: bool = False) -> str:
        """
        Load channel-specific summary content from state directory.
        
        Also backfills missing dates in summaries if agent access is available.
        """
        # Backfill missing dates asynchronously (non-blocking, fire-and-forget)
        # We do this before loading to ensure dates are available on next load
        try:
            if hasattr(self, 'client') and self.client and hasattr(self.client, 'is_connected'):
                # Only trigger backfill if client is connected (during normal agent operations)
                # Skip in admin panel context where client may not be properly initialized
                try:
                    import asyncio
                    # Create task but don't await - let it run in background
                    asyncio.create_task(self._storage.backfill_summary_dates(channel_id, self))
                except Exception as e:
                    logger.debug(f"[{self.name}] Failed to create backfill task: {e}")
        except Exception as e:
            logger.debug(f"[{self.name}] Failed to trigger backfill during load: {e}")
        
        return self._storage.load_summary_content(channel_id, json_format=json_format)

    def get_channel_llm_model(self, channel_id: int) -> str | None:
        """Get the LLM model name for a specific channel from the channel memory file."""
        return self._storage.get_channel_llm_model(channel_id)

    def _load_schedule(self) -> dict | None:
        """
        Load agent's schedule from state directory with caching.
        
        The schedule is cached in memory and only reloaded if:
        - Cache is empty (first load)
        - Schedule file modification time has changed
        - Cache was explicitly invalidated
        
        Returns:
            Schedule dictionary or None if schedule doesn't exist
        """
        from pathlib import Path
        from config import STATE_DIRECTORY
        
        schedule_file = Path(STATE_DIRECTORY) / self.name / "schedule.json"
        
        # Check if file exists
        if not schedule_file.exists():
            self._schedule_cache = None
            self._schedule_cache_mtime = None
            return None
        
        # Get file modification time
        try:
            current_mtime = schedule_file.stat().st_mtime
        except OSError:
            # File doesn't exist or can't be accessed
            self._schedule_cache = None
            self._schedule_cache_mtime = None
            return None
        
        # Check if cache is valid
        if (
            self._schedule_cache is not None
            and self._schedule_cache_mtime is not None
            and self._schedule_cache_mtime == current_mtime
        ):
            # Cache is valid, return cached schedule
            return self._schedule_cache
        
        # Load from disk
        schedule = self._storage.load_schedule()
        
        # Update cache
        self._schedule_cache = schedule
        self._schedule_cache_mtime = current_mtime
        
        return schedule

    def _save_schedule(self, schedule: dict) -> None:
        """
        Save agent's schedule to state directory and invalidate cache.
        
        Args:
            schedule: Schedule dictionary to save
        """
        self._storage.save_schedule(schedule)
        
        # Invalidate cache - it will be reloaded on next access
        self._schedule_cache = None
        self._schedule_cache_mtime = None

