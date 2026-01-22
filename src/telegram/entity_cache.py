# telegram/entity_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Telegram entity caching utility.
"""

import logging
from datetime import UTC, timedelta

from telethon.errors.rpcerrorlist import (  # pyright: ignore[reportMissingImports]
    ChannelPrivateError,
    PeerIdInvalidError,
)

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
        except (PeerIdInvalidError, ChannelPrivateError) as e:
            # Cache the "not found" result to avoid repeated API calls for deleted users/channels
            # ChannelPrivateError occurs when a channel is deleted or the agent is removed from it
            # Use 1-hour TTL for "not found" results to allow retries if the entity becomes
            # available later (e.g., user reactivates account, channel becomes accessible)
            not_found_ttl = timedelta(hours=1)
            not_found_expiration = now + not_found_ttl
            self._cache[entity_id] = (None, not_found_expiration)
            logger.debug(f"[{self.name}] Cached failed lookup for ID {entity_id}: {e}")
            return None
        except ValueError as e:
            # Telethon can raise ValueError with "Could not find the input entity" message
            # when an entity doesn't exist or isn't accessible (e.g., deleted account, blocked user).
            # Treat this the same as PeerIdInvalidError - cache as "not found" to avoid repeated API calls.
            error_msg = str(e)
            if "Could not find the input entity" in error_msg:
                not_found_ttl = timedelta(hours=1)
                not_found_expiration = now + not_found_ttl
                self._cache[entity_id] = (None, not_found_expiration)
                logger.debug(f"[{self.name}] Cached failed lookup for ID {entity_id}: {e}")
                return None
            # Other ValueError instances are treated as transient errors
            logger.warning(f"[{self.name}] Transient error fetching entity {entity_id}: {e}")
            raise
        except Exception as e:
            # Transient errors (network timeouts, rate limits, connection issues, etc.)
            # should not be cached as "not found" - let them propagate so callers can retry
            logger.warning(f"[{self.name}] Transient error fetching entity {entity_id}: {e}")
            raise

        self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
        return entity

    def clear(self):
        """Clear all cached entities."""
        logger.info(f"[{self.name}] Clearing entity cache.")
        self._cache.clear()
