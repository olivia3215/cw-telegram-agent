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
            self._entity_cache_obj = TelegramEntityCache(self.client, name=self.name)
        return self._entity_cache_obj

    @property
    def api_cache(self):
        """
        Get or create the TelegramAPICache for this agent.
        
        Returns:
            TelegramAPICache instance, or None if no client available
        """
        if self._api_cache_obj is None and self.client:
            self._api_cache_obj = TelegramAPICache(self.client, name=self.name)
        return self._api_cache_obj

    def clear_entity_cache(self):
        """Clears the entity cache for this agent."""
        logger.info(f"Clearing entity cache for agent {self.name}.")
        if self._entity_cache_obj:
            self._entity_cache_obj.clear()

    async def is_muted(self, peer_id: int) -> bool:
        """
        Checks if a peer is muted, using a 60-second cache.
        """
        api_cache = self.api_cache
        if not api_cache:
            return False
        return await api_cache.is_muted(peer_id)

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

    async def get_dialog(self, chat_id: int):
        """
        Finds a dialog, preferring the agent's entity cache.
        """
        async for dialog in self.client.iter_dialogs():
            if dialog.id == chat_id:
                return dialog
        return None

