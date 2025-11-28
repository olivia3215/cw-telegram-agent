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

logger = logging.getLogger(__name__)


class TelegramEntityCache:
    """
    Caches Telegram entities to avoid excessive API calls.
    
    Entities are cached for a configurable TTL (default 5 minutes).
    """

    def __init__(self, client, ttl_seconds=300, name=None):
        """
        Initialize the entity cache.
        
        Args:
            client: The Telegram client to use for fetching entities
            ttl_seconds: Time-to-live for cached entities in seconds (default: 300 = 5 minutes)
            name: Optional name for logging/debugging
        """
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.name = name or "entity_cache"
        self._cache = {}  # {entity_id: (entity, expiration_time)}

    async def get(self, entity_id: int):
        """
        Get a Telegram entity, using cache if available.
        
        Args:
            entity_id: The entity ID to fetch
            
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
            entity = await self.client.get_entity(entity_id)
            # Cache for the configured TTL
            self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
            return entity
        except Exception as e:
            logger.exception(f"[{self.name}] get_cached_entity failed for ID {entity_id}: {e}")
            # On error, return None and don't cache
            return None

    def clear(self):
        """Clear all cached entities."""
        logger.info(f"[{self.name}] Clearing entity cache.")
        self._cache.clear()


