# agent/telegram.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram API interactions for Agent.
"""

import logging
from typing import TYPE_CHECKING

from telegram.api_cache import TelegramAPICache
from telegram.dialog_cache import TelegramDialogCache
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
    _dialog_cache_obj: TelegramDialogCache | None

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

    @property
    def dialog_cache(self):
        """
        Get or create the TelegramDialogCache for this agent.
        
        Returns:
            TelegramDialogCache instance, or None if no client available
        """
        if self._dialog_cache_obj is None and self.client:
            # Pass self (the agent) so cache can use agent's reconnection logic
            self._dialog_cache_obj = TelegramDialogCache(self.client, name=self.name, agent=self)
        return self._dialog_cache_obj

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
        Finds a dialog, preferring the agent's dialog cache.
        
        This method first checks the dialog cache. If the cache is stale or the
        dialog is not found, it falls back to iterating all dialogs (which will
        trigger GetHistoryRequest calls and may cause flood waits).
        
        For better performance, ensure the dialog cache is populated by calling
        dialog_cache.update_from_iter_dialogs() periodically (e.g., during
        scan_unread_messages).
        """
        # Try cache first
        dialog_cache = self.dialog_cache
        if dialog_cache:
            cached_dialog = await dialog_cache.get(chat_id)
            if cached_dialog:
                logger.debug(f"[{self.name}] get_dialog({chat_id}) found in cache - avoiding iter_dialogs()")
                return cached_dialog
        
        # Cache miss or no cache - fall back to iterating (expensive!)
        # This will trigger GetHistoryRequest calls
        logger.debug(f"[{self.name}] get_dialog({chat_id}) cache miss - calling iter_dialogs() - will trigger GetHistoryRequest")
        async for dialog in self.client.iter_dialogs():
            if dialog.id == chat_id:
                logger.debug(f"[{self.name}] get_dialog({chat_id}) found via iter_dialogs()")
                return dialog
        logger.debug(f"[{self.name}] get_dialog({chat_id}) not found via iter_dialogs()")
        return None

