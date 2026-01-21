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
        
        Also caches "not found" results (None) to avoid repeated API calls
        for deleted users or entities that don't exist.
        
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
            if self.agent:
                await self.agent.ensure_client_connected()
            entity = await self.client.get_entity(entity_id)
        except Exception as e:
            # Cache the "not found" result to avoid repeated API calls for deleted users
            # Use infinite TTL (far-future expiration) since deleted accounts won't come back
            # Set expiration to 10 years in the future to effectively make it permanent
            infinite_expiration = now + timedelta(days=365 * 10)
            self._cache[entity_id] = (None, infinite_expiration)
            logger.debug(f"[{self.name}] Cached failed lookup for ID {entity_id}: {e}")
            return None

        self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
        return entity

    def clear(self):
        """Clear all cached entities."""
        logger.info(f"[{self.name}] Clearing entity cache.")
        self._cache.clear()
