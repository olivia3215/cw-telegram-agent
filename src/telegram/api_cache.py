# telegram/api_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram API caching utility for mute status and blocklist.
"""

import logging
from datetime import UTC, datetime, timedelta

from telethon.tl.functions.account import GetNotifySettingsRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.contacts import GetBlockedRequest  # pyright: ignore[reportMissingImports]

from clock import clock

logger = logging.getLogger(__name__)


class TelegramAPICache:
    """
    Caches Telegram API responses for mute status and blocklist.
    
    Provides cached access to:
    - Mute status (per-peer, 60-second TTL)
    - Blocklist (global, 60-second TTL)
    """

    def __init__(self, client, name=None, agent=None):
        """
        Initialize the API cache.
        
        Args:
            client: The Telegram client to use for API calls
            name: Optional name for logging/debugging
            agent: Optional agent instance for reconnection handling
        """
        self.client = client
        self.agent = agent
        self.name = name or "api_cache"
        self._mute_cache = {}  # {peer_id: (is_muted, expiration_time)}
        self._blocklist_cache = None
        self._blocklist_last_updated = None

    async def is_muted(self, peer_id: int, ttl_seconds=60) -> bool:
        """
        Check if a peer is muted, using cache.
        
        Args:
            peer_id: The peer ID to check
            ttl_seconds: Cache TTL in seconds (default: 60)
            
        Returns:
            True if the peer is muted, False otherwise
        """
        assert isinstance(peer_id, int)
        now = clock.now(UTC)
        cached = self._mute_cache.get(peer_id)
        if cached and cached[1] > now:
            return cached[0]

        if not self.client:
            self._mute_cache[peer_id] = (False, now + timedelta(seconds=15))
            return False

        # Check if agent is disabled - don't attempt reconnection if disabled
        if self.agent and self.agent.is_disabled:
            self._mute_cache[peer_id] = (False, now + timedelta(seconds=15))
            return False

        try:
            # Attempt to ensure client is connected (will reconnect if needed)
            if self.agent:
                if not await self.agent.ensure_client_connected():
                    # Reconnection failed - return cached/default value
                    self._mute_cache[peer_id] = (False, now + timedelta(seconds=15))
                    return False
            settings = await self.client(GetNotifySettingsRequest(peer=peer_id))
        except Exception as e:
            logger.exception(f"[{self.name}] is_muted failed for peer {peer_id}: {e}")
            self._mute_cache[peer_id] = (False, now + timedelta(seconds=15))
            return False

        mute_until = getattr(settings, "mute_until", None)
        is_currently_muted = False
        if isinstance(mute_until, datetime):
            is_currently_muted = mute_until > now
        elif isinstance(mute_until, int):
            is_currently_muted = mute_until > now.timestamp()

        self._mute_cache[peer_id] = (is_currently_muted, now + timedelta(seconds=ttl_seconds))
        return is_currently_muted

    async def is_blocked(self, user_id: int, ttl_seconds=60) -> bool:
        """
        Check if a user is blocked, using cache.
        
        Args:
            user_id: The user ID to check
            ttl_seconds: Cache TTL in seconds (default: 60)
            
        Returns:
            True if the user is blocked, False otherwise
        """
        now = clock.now()
        # Invalidate cache if expired
        if self._blocklist_cache is None or (
            self._blocklist_last_updated
            and (now - self._blocklist_last_updated) > timedelta(seconds=ttl_seconds)
        ):
            if not self.client:
                if self._blocklist_cache is None:
                    self._blocklist_cache = set()
                return user_id in self._blocklist_cache

            try:
                # Check if agent is disabled - don't attempt reconnection if disabled
                if self.agent and self.agent.is_disabled:
                    if self._blocklist_cache is None:
                        self._blocklist_cache = set()
                    return user_id in self._blocklist_cache
                
                # Attempt to ensure client is connected (will reconnect if needed)
                if self.agent:
                    if not await self.agent.ensure_client_connected():
                        # Reconnection failed - return cached/default value
                        if self._blocklist_cache is None:
                            self._blocklist_cache = set()
                        return user_id in self._blocklist_cache
                result = await self.client(GetBlockedRequest(offset=0, limit=100))
            except Exception as e:
                logger.exception(f"[{self.name}] Failed to update blocklist: {e}")
                if self._blocklist_cache is None:
                    self._blocklist_cache = set()
                return user_id in self._blocklist_cache

            self._blocklist_cache = {item.peer_id.user_id for item in result.blocked}
            self._blocklist_last_updated = now
            logger.debug(f"[{self.name}] Updated blocklist cache.")

        return user_id in self._blocklist_cache
