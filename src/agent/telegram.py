# agent/telegram.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram API interactions for Agent.
"""

import logging
from typing import TYPE_CHECKING

from telegram.api_cache import TelegramAPICache
from telegram.entity_cache import TelegramEntityCache

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent


class AgentTelegramMixin:
    """Mixin providing Telegram API interaction capabilities."""

    _client: object | None
    name: str
    _entity_cache_obj: TelegramEntityCache | None
    _api_cache_obj: TelegramAPICache | None

    @property
    def entity_cache(self):
        """
        Get or create the TelegramEntityCache for this agent.
        
        Returns:
            TelegramEntityCache instance, or None if no client available
        """
        if self._entity_cache_obj is None and self.client:
            # Pass self (the agent) so cache can use agent's reconnection logic
            self._entity_cache_obj = TelegramEntityCache(self.client, name=self.name, agent=self)
        return self._entity_cache_obj

    @property
    def api_cache(self):
        """
        Get or create the TelegramAPICache for this agent.
        
        Returns:
            TelegramAPICache instance, or None if no client available
        """
        if self._api_cache_obj is None and self.client:
            # Pass self (the agent) so cache can use agent's reconnection logic
            self._api_cache_obj = TelegramAPICache(self.client, name=self.name, agent=self)
        return self._api_cache_obj

    def clear_entity_cache(self):
        """Clears the entity cache for this agent."""
        logger.info(f"Clearing entity cache for agent {self.name}.")
        if self._entity_cache_obj:
            self._entity_cache_obj.clear()

    def clear_client_and_caches(self):
        """
        Clear the client reference and all cache objects that hold references to it.
        
        This should be called whenever the client is being cleared (e.g., on disconnect,
        disable, or reconnection) to ensure cache objects don't retain stale client references.
        
        Also clears the executor to prevent it from holding references to stale event loops.
        Also clears storage object to ensure it is recreated with the correct backend
        (filesystem vs MySQL) based on the current agent_id after authentication.
        """
        self._client = None
        self._loop = None  # Clear cached loop
        self._executor = None  # Clear executor to prevent stale event loop references
        # Clear cache objects that hold references to the old client
        self._api_cache_obj = None
        self._entity_cache_obj = None
        # Clear storage object so it is recreated with correct backend after authentication
        self._storage_obj = None

    async def is_muted(self, peer_id: int) -> bool:
        """
        Checks if a peer is muted, using a 60-second cache.
        """
        api_cache = self.api_cache
        if not api_cache:
            return False
        return await api_cache.is_muted(peer_id)

    async def is_conversation_gagged(self, channel_id: int) -> bool:
        """
        Checks if a conversation is gagged.
        
        Returns True if:
        - Global gagged flag is True AND no per-conversation override exists, OR
        - Per-conversation override is True
        
        Returns False if:
        - Global gagged flag is False AND no per-conversation override exists, OR
        - Per-conversation override is False
        
        Args:
            channel_id: The channel ID to check
            
        Returns:
            True if gagged, False otherwise
        """
        if not self.agent_id:
            # Not authenticated, use global default
            return self.is_gagged
        
        try:
            from db import conversation_gagged
            override = conversation_gagged.get_conversation_gagged(self.agent_id, channel_id)
            if override is not None:
                # Per-conversation override exists, use it
                return override
            else:
                # No override, use global default
                return self.is_gagged
        except Exception as e:
            logger.warning(f"[{self.name}] Error checking gagged status for channel {channel_id}: {e}")
            # On error, use global default
            return self.is_gagged

    async def get_cached_entity(self, entity_id: int):
        """
        Return a Telegram entity.
        
        This method caches entities for 5 minutes to avoid excessive API calls.
        Callers should ensure they're running in the client's event loop (handlers
        are automatically routed to the client's event loop by the task dispatcher).
        """
        entity_cache = self.entity_cache
        if not entity_cache:
            return None
        return await entity_cache.get(entity_id)

    async def is_blocked(self, user_id):
        """
        Checks if a user is in the agent's blocklist, using a short-lived cache
        to avoid excessive API calls.
        """
        api_cache = self.api_cache
        if not api_cache:
            return False
        return await api_cache.is_blocked(user_id)
