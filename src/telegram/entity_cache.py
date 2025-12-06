# telegram/entity_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram entity caching utility.
"""

import logging
from datetime import UTC, timedelta

from clock import clock
from utils import normalize_peer_id
from telegram.secret_chat import get_secret_chat_entity

logger = logging.getLogger(__name__)


class TelegramEntityCache:
    """
    Caches Telegram entities to avoid excessive API calls.
    
    Entities are cached for a configurable TTL (default 5 minutes).
    """

    def __init__(self, client, ttl_seconds=300, name=None, agent=None):
        """
        Initialize the entity cache.
        
        Args:
            client: The Telegram client to use for fetching entities
            ttl_seconds: Time-to-live for cached entities in seconds (default: 300 = 5 minutes)
            name: Optional name for logging/debugging
            agent: Optional agent instance for reconnection handling
        """
        self.client = client
        self.agent = agent
        self.ttl_seconds = ttl_seconds
        self.name = name or "entity_cache"
        self._cache = {}  # {entity_id: (entity, expiration_time)}

    async def get(self, entity_id: int):
        """
        Get a Telegram entity, using cache if available.
        
        Supports both regular entities and secret chats.
        
        Args:
            entity_id: The entity ID to fetch (may be a secret chat ID)
            
        Returns:
            The Telegram entity, or None if not found or on error
        """
        entity_id = normalize_peer_id(entity_id)

        now = clock.now(UTC)
        cached = self._cache.get(entity_id)
        if cached and cached[1] > now:
            return cached[0]

        if not self.client:
            return None

        try:
            if self.agent:
                await self.agent.ensure_client_connected()
            
            # Check if this is a secret chat ID (format: -2000000000 - chat_id)
            if entity_id < -2000000000:
                # This is a secret chat ID, try to resolve it
                entity = await get_secret_chat_entity(self.client, entity_id)
                if entity:
                    self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
                    return entity
                # If secret chat resolution fails, fall through to regular get_entity
            
            # Regular entity lookup
            entity = await self.client.get_entity(entity_id)
        except Exception as e:
            logger.exception(f"[{self.name}] get_cached_entity failed for ID {entity_id}: {e}")
            return None

        self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
        return entity

    def clear(self):
        """Clear all cached entities."""
        logger.info(f"[{self.name}] Clearing entity cache.")
        self._cache.clear()


