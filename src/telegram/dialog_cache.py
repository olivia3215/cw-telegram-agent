# telegram/dialog_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram dialog caching utility.

Caches dialogs from iter_dialogs() to avoid repeated GetHistoryRequest calls.
"""

import logging
from datetime import UTC, timedelta

from clock import clock
from utils import normalize_peer_id

logger = logging.getLogger(__name__)


class TelegramDialogCache:
    """
    Caches Telegram dialogs to avoid excessive API calls.
    
    Dialogs are cached for a configurable TTL (default 2 minutes).
    The cache is populated by calling update_from_iter_dialogs().
    """

    def __init__(self, client, ttl_seconds=120, name=None, agent=None):
        """
        Initialize the dialog cache.
        
        Args:
            client: The Telegram client to use for fetching dialogs
            ttl_seconds: Time-to-live for cached dialogs in seconds (default: 120 = 2 minutes)
            name: Optional name for logging/debugging
            agent: Optional agent instance for reconnection handling
        """
        self.client = client
        self.agent = agent
        self.ttl_seconds = ttl_seconds
        self.name = name or "dialog_cache"
        self._cache = {}  # {dialog_id: (dialog, expiration_time)}
        self._last_update = None  # When the cache was last populated

    async def get(self, dialog_id: int):
        """
        Get a Telegram dialog by ID, using cache if available.
        
        Args:
            dialog_id: The dialog ID to fetch
            
        Returns:
            The Telegram dialog, or None if not found or cache expired
        """
        dialog_id = normalize_peer_id(dialog_id)

        now = clock.now(UTC)
        cached = self._cache.get(dialog_id)
        if cached and cached[1] > now:
            return cached[0]

        # Cache miss or expired - return None
        # Caller should fall back to iter_dialogs() or update cache first
        return None

    async def update_from_iter_dialogs(self):
        """
        Populate the cache by iterating through all dialogs.
        
        This should be called periodically (e.g., during scan_unread_messages)
        to keep the cache fresh.
        
        Returns:
            Number of dialogs cached
        """
        if not self.client:
            return 0

        try:
            if self.agent:
                await self.agent.ensure_client_connected()
            
            now = clock.now(UTC)
            expiration = now + timedelta(seconds=self.ttl_seconds)
            count = 0
            
            async for dialog in self.client.iter_dialogs():
                dialog_id = normalize_peer_id(dialog.id)
                self._cache[dialog_id] = (dialog, expiration)
                count += 1
            
            self._last_update = now
            logger.debug(f"[{self.name}] Cached {count} dialogs (TTL: {self.ttl_seconds}s)")
            return count
            
        except Exception as e:
            logger.exception(f"[{self.name}] Failed to update dialog cache: {e}")
            return 0

    def clear(self):
        """Clear all cached dialogs."""
        logger.info(f"[{self.name}] Clearing dialog cache.")
        self._cache.clear()
        self._last_update = None

    def is_stale(self):
        """
        Check if the cache is stale (needs updating).
        
        Returns:
            True if cache is empty or expired, False otherwise
        """
        if not self._cache:
            return True
        
        if self._last_update is None:
            return True
        
        now = clock.now(UTC)
        return (now - self._last_update).total_seconds() > self.ttl_seconds

    def get_all_cached_dialogs(self):
        """
        Get all cached dialogs that are still valid.
        
        Returns:
            List of dialog objects that are still cached and not expired
        """
        now = clock.now(UTC)
        dialogs = []
        for dialog_id, (dialog, expiration) in self._cache.items():
            if expiration > now:
                dialogs.append(dialog)
        return dialogs

